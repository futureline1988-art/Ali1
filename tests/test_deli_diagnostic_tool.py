"""Tests for tools/diagnostics/deli_es172_diagnose.py -- the standalone,
stdlib-only field diagnostic used to investigate the DELI ES172's
unknown local network protocol.

This tool intentionally never speaks a guessed DELI protocol -- it only
does generic, protocol-agnostic TCP/HTTP/WebSocket fingerprinting (see
the tool's own module docstring). These tests prove that fingerprinting
logic actually distinguishes an HTTP service from a raw-binary service
from a closed port, using local throwaway test servers rather than the
real (inaccessible from this environment) device.
"""

from __future__ import annotations

import http.server
import socket
import threading
import time

import pytest

from tools.diagnostics import deli_es172_diagnose as diag


@pytest.fixture
def free_ports():
    """Reserve three free TCP ports on 127.0.0.1: HTTP, raw-banner, and closed."""
    sockets = []
    ports = []
    for _ in range(3):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        sockets.append(s)
        ports.append(s.getsockname()[1])
    for s in sockets:
        s.close()  # release immediately so the servers below can bind them
    return ports


@pytest.fixture
def http_test_server(free_ports):
    port = free_ports[0]
    server = http.server.HTTPServer(("127.0.0.1", port), http.server.SimpleHTTPRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()


@pytest.fixture
def raw_banner_server(free_ports):
    """A TCP server that sends an unsolicited binary banner then closes -- simulates a proprietary protocol."""
    port = free_ports[1]
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("127.0.0.1", port))
    server_socket.listen(5)
    stop_requested = threading.Event()

    def serve():
        server_socket.settimeout(0.2)
        while not stop_requested.is_set():
            try:
                conn, _ = server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break  # socket closed during shutdown
            try:
                conn.sendall(b"\x02\x00\x01\xffDELIPROTO\x00\x00")
            finally:
                conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield port
    stop_requested.set()
    server_socket.close()


@pytest.fixture
def closed_port(free_ports):
    return free_ports[2]


class TestProbeTcpPort:
    def test_open_port_is_detected_as_open(self, http_test_server):
        result = diag.probe_tcp_port("127.0.0.1", http_test_server)
        assert result["open"] is True

    def test_closed_port_is_detected_as_closed(self, closed_port):
        result = diag.probe_tcp_port("127.0.0.1", closed_port)
        assert result["open"] is False
        assert "error" in result

    def test_unsolicited_banner_is_captured(self, raw_banner_server):
        time.sleep(0.1)
        result = diag.probe_tcp_port("127.0.0.1", raw_banner_server)
        assert result["open"] is True
        assert result["unsolicited_banner_bytes"] == 15  # exact length of the test banner
        # Non-UTF-8 bytes (0xff) fall back to a hex snippet -- see _decode_snippet.
        assert result["unsolicited_banner_snippet"] is not None
        assert len(result["unsolicited_banner_snippet"]) > 0


class TestHttpDetection:
    def test_http_server_is_detected_as_http(self, http_test_server):
        result = diag.try_http_get("127.0.0.1", http_test_server, "/", use_tls=False)
        assert result["looks_like_http"] is True
        assert result["status_line"].startswith("HTTP/")

    def test_raw_binary_server_is_not_detected_as_http(self, raw_banner_server):
        time.sleep(0.1)
        result = diag.try_http_get("127.0.0.1", raw_banner_server, "/", use_tls=False)
        assert not result.get("looks_like_http")

    def test_closed_port_reports_an_error_not_a_crash(self, closed_port):
        result = diag.try_http_get("127.0.0.1", closed_port, "/", use_tls=False)
        assert "error" in result
        assert not result.get("looks_like_http")


class TestWebSocketDetection:
    def test_plain_http_server_does_not_return_101(self, http_test_server):
        result = diag.try_websocket_handshake("127.0.0.1", http_test_server)
        assert result["is_101_switching_protocols"] is False

    def test_raw_binary_server_does_not_return_101(self, raw_banner_server):
        time.sleep(0.1)
        result = diag.try_websocket_handshake("127.0.0.1", raw_banner_server)
        assert result["is_101_switching_protocols"] is False


class TestFullDiagnoseFlow:
    def test_diagnose_never_sends_a_credential_and_produces_a_report_for_every_port(
        self, http_test_server, raw_banner_server, closed_port, monkeypatch
    ):
        monkeypatch.setattr(diag, "CANDIDATE_PORTS", [http_test_server, raw_banner_server, closed_port])
        monkeypatch.setattr(diag, "WEBSOCKET_PROBE_PORTS", {http_test_server, raw_banner_server})
        time.sleep(0.1)

        report = diag.diagnose("127.0.0.1")

        assert len(report["ports"]) == 3
        by_port = {entry["port"]: entry for entry in report["ports"]}
        assert by_port[http_test_server]["open"] is True
        assert by_port[raw_banner_server]["open"] is True
        assert by_port[closed_port]["open"] is False

        # The report is pure JSON-serializable data -- confirms nothing
        # secret (there is nothing secret to begin with: this tool takes
        # no API key/device UID as input at all) ever entered it.
        import json

        serialized = json.dumps(report)
        assert "api_key" not in serialized.lower()
        assert "device_uid" not in serialized.lower()


def test_write_reports_creates_both_json_and_txt(tmp_path, monkeypatch):
    fake_report = {
        "generated_at_utc": "2026-01-01T00:00:00Z",
        "target_ip": "192.168.1.28",
        "runner_platform": "test",
        "ping": {"reachable": False},
        "ports": [
            {"port": 5005, "open": False, "error": "timed out"},
        ],
    }
    monkeypatch.setattr(diag, "__file__", str(tmp_path / "deli_es172_diagnose.py"))
    json_path, txt_path = diag._write_reports(fake_report)
    assert json_path.exists()
    assert txt_path.exists()
    assert json_path.parent.name == "deli_diagnostic_output"
    assert "5005" in txt_path.read_text(encoding="utf-8")

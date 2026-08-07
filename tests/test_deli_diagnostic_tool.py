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


_DEV_INTERFACE_HTML = """<!DOCTYPE html>
<html>
<head>
<title>Attendance &amp; Access machine development test interface</title>
<link rel="stylesheet" href="/style.css">
<script src="/app.js"></script>
<script src="http://evil.example.com/cross-origin.js"></script>
</head>
<body>
<a href="/api/employees">Employees</a>
<form action="/set_wifi" method="POST">
<input name="ssid" type="text">
<input name="password" type="password">
</form>
<button id="enrollBtn" type="button">Enroll</button>
<script>
fetch('/api/status');
var ws = new WebSocket('ws://192.168.1.28:5005/socket');
var apiKey = "secret123";
</script>
</body>
</html>
"""

_DEV_INTERFACE_JS = """var xhr = new XMLHttpRequest();
xhr.open('GET', '/api/devices');
xhr.send();
var enrollEndpoint = "/api/enroll";
"""

_DEV_INTERFACE_CSS = "body { color: red; }\n"


class _DevInterfaceRequestHandler(http.server.BaseHTTPRequestHandler):
    """Serves the crafted dev-interface fixture pages for TestInvestigateHttpDevInterface."""

    _ROUTES = {
        "/": ("text/html", _DEV_INTERFACE_HTML),
        "/app.js": ("application/javascript", _DEV_INTERFACE_JS),
        "/style.css": ("text/css", _DEV_INTERFACE_CSS),
    }

    def log_message(self, format, *args):  # noqa: A002 - matches BaseHTTPRequestHandler's signature
        pass  # silence per-request logging in test output

    def do_GET(self):
        route = self._ROUTES.get(self.path)
        if route is None:
            self.send_response(404)
            self.end_headers()
            return
        content_type, body_text = route
        body = body_text.encode("utf-8")
        self.send_response(200)
        self.send_header("Server", "Boost.Beast/144")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def dev_interface_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _DevInterfaceRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port
    server.shutdown()


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


class TestDevInterfaceHTMLParser:
    """_DevInterfaceHTMLParser -- pure HTML parsing, no network involved."""

    def test_extracts_links_forms_inputs_buttons_scripts_and_stylesheets(self):
        html = (
            "<html><head>"
            '<link rel="stylesheet" href="/style.css">'
            '<script src="/app.js"></script>'
            "</head><body>"
            '<a href="/api/employees">Employees</a>'
            '<form action="/set_wifi" method="POST">'
            '<input name="ssid" type="text">'
            "</form>"
            '<button id="enrollBtn" type="button">Enroll</button>'
            "<script>var x = 1;</script>"
            "</body></html>"
        )
        parser = diag._DevInterfaceHTMLParser()
        parser.feed(html)

        assert parser.links == ["/api/employees"]
        assert parser.stylesheets == ["/style.css"]
        assert parser.script_srcs == ["/app.js"]
        assert len(parser.forms) == 1
        assert parser.forms[0]["action"] == "/set_wifi"
        assert parser.forms[0]["method"] == "POST"
        assert parser.forms[0]["inputs"] == [{"name": "ssid", "type": "text"}]
        assert len(parser.buttons) == 1
        assert parser.buttons[0]["id"] == "enrollBtn"
        assert parser.inline_scripts == ["var x = 1;"]

    def test_malformed_html_does_not_raise(self):
        parser = diag._DevInterfaceHTMLParser()
        parser.feed("<div><span>unclosed<script>var x = <>;")  # never raises


class TestExtractionHelpers:
    """The regex-based JS/HTML text extraction helpers, in isolation."""

    def test_endpoint_like_strings(self):
        text = "const a = \"/api/employees\"; const b = '/set_wifi'; const c = \"not-a-path\";"
        result = diag._extract_endpoint_like_strings(text)
        assert "/api/employees" in result
        assert "/set_wifi" in result
        assert "not-a-path" not in result

    def test_fetch_calls(self):
        text = "fetch('/api/status'); fetch(\"/api/other\");"
        result = diag._extract_fetch_calls(text)
        assert "/api/status" in result
        assert "/api/other" in result

    def test_xhr_calls(self):
        text = "xhr.open('GET', '/api/devices'); xhr2.open(\"POST\", \"/api/enroll\");"
        result = diag._extract_xhr_calls(text)
        assert {"method": "GET", "url": "/api/devices"} in result
        assert {"method": "POST", "url": "/api/enroll"} in result

    def test_websocket_urls(self):
        text = "var ws = new WebSocket('ws://192.168.1.28:5005/socket');"
        result = diag._extract_websocket_urls(text)
        assert "ws://192.168.1.28:5005/socket" in result

    def test_auth_hints_surface_likely_credential_lines_only(self):
        text = 'var apiKey = "secret123";\nvar normal = 1;\nheaders["Authorization"] = "Bearer x";'
        hints = diag._extract_auth_hints(text)
        assert any("apiKey" in h for h in hints)
        assert any("Authorization" in h for h in hints)
        assert not any("normal" in h for h in hints)


class TestDechunk:
    def test_passthrough_when_not_chunked(self):
        body = b"hello world"
        assert diag._dechunk_if_needed(["Content-Type: text/plain"], body) == body

    def test_decodes_a_chunked_body(self):
        chunked = b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n"
        result = diag._dechunk_if_needed(["Transfer-Encoding: chunked"], chunked)
        assert result == b"hello world"

    def test_falls_back_to_raw_bytes_on_malformed_chunk_data(self):
        headers = ["Transfer-Encoding: chunked"]
        malformed = b"not-a-valid-chunk-body"
        assert diag._dechunk_if_needed(headers, malformed) == malformed


class TestSameOriginRequestPath:
    def test_relative_path_is_same_origin(self):
        assert diag._same_origin_request_path("/app.js") == "/app.js"

    def test_relative_path_with_query_is_preserved(self):
        assert diag._same_origin_request_path("/app.js?v=2") == "/app.js?v=2"

    def test_absolute_cross_origin_url_is_rejected(self):
        assert diag._same_origin_request_path("http://evil.example.com/x.js") is None

    def test_protocol_relative_url_is_rejected(self):
        assert diag._same_origin_request_path("//evil.example.com/x.js") is None

    def test_none_is_rejected(self):
        assert diag._same_origin_request_path(None) is None


class TestInvestigateHttpDevInterface:
    """End-to-end against a local server serving a crafted dev-interface
    page -- proves the whole read-only investigation (fetch page, parse
    HTML, follow same-origin resources, extract findings) against real
    HTTP responses, not just the individual helpers."""

    def test_full_investigation_of_the_crafted_dev_interface(self, dev_interface_server):
        result = diag.investigate_http_dev_interface("127.0.0.1", dev_interface_server)

        assert "error" not in result
        assert "Attendance" in result["root_page"]["body_text"]
        assert result["links"] == ["/api/employees"]
        assert len(result["forms"]) == 1
        assert result["forms"][0]["action"] == "/set_wifi"
        assert len(result["buttons"]) == 1
        assert result["buttons"][0]["id"] == "enrollBtn"

        # Same-origin app.js and style.css are followed; the cross-origin
        # script is not -- exactly two fetched resources, never three.
        assert len(result["fetched_resources"]) == 2
        fetched_paths = {r["path"] for r in result["fetched_resources"]}
        assert fetched_paths == {"/app.js", "/style.css"}

        app_js = next(r for r in result["fetched_resources"] if r["path"] == "/app.js")
        assert {"method": "GET", "url": "/api/devices"} in app_js["findings"]["xhr_calls"]
        assert "/api/enroll" in app_js["findings"]["endpoint_like_strings"]

        assert "/api/status" in result["inline_script_findings"]["fetch_calls"]
        assert "ws://192.168.1.28:5005/socket" in result["inline_script_findings"]["websocket_urls"]
        assert result["mentions_port_5005"] is True
        assert any("apiKey" in hint for hint in result["inline_script_findings"]["auth_hints"])

    def test_never_follows_the_cross_origin_script(self, dev_interface_server):
        result = diag.investigate_http_dev_interface("127.0.0.1", dev_interface_server)
        referenced = {r["referenced_as"] for r in result["fetched_resources"]}
        assert "http://evil.example.com/cross-origin.js" not in referenced

    def test_wired_into_diagnose_for_an_http_port(self, dev_interface_server, monkeypatch):
        monkeypatch.setattr(diag, "CANDIDATE_PORTS", [dev_interface_server])
        monkeypatch.setattr(diag, "WEBSOCKET_PROBE_PORTS", set())

        report = diag.diagnose("127.0.0.1")

        entry = report["ports"][0]
        assert entry["open"] is True
        assert "dev_interface" in entry
        assert entry["dev_interface"]["mentions_port_5005"] is True


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


def test_write_reports_honors_an_explicit_output_dir(tmp_path):
    """The in-app caller (ui.devices.NetworkDiagnosticDialog) passes an
    explicit, guaranteed-writable directory rather than relying on the
    default "next to the script" location, which is a temporary,
    discarded-on-exit extraction directory in a frozen onefile build.
    """
    fake_report = {
        "generated_at_utc": "2026-01-01T00:00:00Z",
        "target_ip": "192.168.1.28",
        "runner_platform": "test",
        "ping": {"reachable": True},
        "ports": [{"port": 5005, "open": False, "error": "timed out"}],
    }
    output_dir = tmp_path / "app_data" / "diagnostics"
    json_path, txt_path = diag._write_reports(fake_report, output_dir=output_dir)
    assert json_path.parent == output_dir
    assert txt_path.parent == output_dir
    assert json_path.exists()
    assert txt_path.exists()


class TestFormatNetworkDiagnosticSummary:
    """ui.devices._format_network_diagnostic_summary -- a pure function
    turning a diagnose() report into a plain-language Arabic summary,
    tested without any Qt widget involved."""

    def test_summarizes_reachability_and_open_closed_ports(self):
        from ui.devices import _format_network_diagnostic_summary

        report = {
            "target_ip": "192.168.1.28",
            "ping": {"reachable": True},
            "ports": [
                {
                    "port": 5005,
                    "open": True,
                    "unsolicited_banner_bytes": 12,
                    "http_probes": [{"looks_like_http": False}],
                    "websocket_probe": {"is_101_switching_protocols": True},
                },
                {"port": 4370, "open": False, "error": "timed out"},
            ],
        }
        summary = _format_network_diagnostic_summary(report)
        assert "192.168.1.28" in summary
        assert "نعم" in summary  # reachable
        assert "5005" in summary and "مفتوح" in summary
        assert "4370" in summary and "مغلق" in summary
        assert "WebSocket" in summary

    def test_says_nothing_it_did_not_observe(self):
        from ui.devices import _format_network_diagnostic_summary

        report = {
            "target_ip": "10.0.0.5",
            "ping": {"reachable": False},
            "ports": [{"port": 80, "open": True, "http_probes": []}],
        }
        summary = _format_network_diagnostic_summary(report)
        assert "WebSocket" not in summary
        assert "HTTP" not in summary


class TestNetworkDiagnosticDialog:
    """ui.devices.NetworkDiagnosticDialog -- the in-app diagnostic that
    needs no separately installed Python (see the Windows-packaging fix
    this dialog was added for: the standalone .bat script requires a
    system Python, which a normal customer install does not have)."""

    @pytest.fixture(autouse=True)
    def _cleanup_diagnostics_dir(self):
        import shutil

        import config as attendance_config_module
        from config import get_config

        output_dir = get_config().paths.data_dir / "diagnostics"
        if output_dir.exists():
            shutil.rmtree(output_dir)
        yield
        if output_dir.exists():
            shutil.rmtree(output_dir)
        # get_config() above lazily creates config's process-wide
        # singleton, same as tests/conftest.py's db_session fixture
        # does for every DB-using test -- reset it here too so a test
        # that asserts nothing has touched it yet (e.g.
        # test_developer_suite_phase2.py's isolation check) is not
        # affected just because this file happens to sort before it.
        attendance_config_module._config_instance = None

    def test_default_ip_is_prefilled_and_folder_button_starts_disabled(self, qtbot):
        from ui.devices import NetworkDiagnosticDialog

        dialog = NetworkDiagnosticDialog()
        qtbot.addWidget(dialog)
        assert dialog.ip_edit.text() == diag.DEFAULT_TARGET_IP
        assert dialog.open_folder_button.isEnabled() is False

    def test_running_the_diagnostic_populates_results_and_writes_reports(self, qtbot, monkeypatch):
        from ui import devices as devices_module

        fake_report = {
            "target_ip": "192.168.1.28",
            "generated_at_utc": "2026-01-01T00:00:00Z",
            "runner_platform": "test",
            "ping": {"reachable": True},
            "ports": [{"port": 5005, "open": False, "error": "timed out"}],
        }
        # Patches the same module object ui.devices imported as
        # network_diagnostic -- no real socket I/O happens in this test.
        monkeypatch.setattr(devices_module.network_diagnostic, "diagnose", lambda ip: fake_report)

        dialog = devices_module.NetworkDiagnosticDialog()
        qtbot.addWidget(dialog)
        dialog.run_button.click()

        assert "192.168.1.28" in dialog.result_view.toPlainText()
        assert dialog.open_folder_button.isEnabled() is True
        assert dialog._last_output_dir is not None
        assert list(dialog._last_output_dir.glob("*.json"))
        assert list(dialog._last_output_dir.glob("*.txt"))

    def test_empty_ip_is_rejected_before_running_anything(self, qtbot, monkeypatch):
        from ui import devices as devices_module

        called = False

        def _fake_diagnose(ip):
            nonlocal called
            called = True
            return {}

        monkeypatch.setattr(devices_module.network_diagnostic, "diagnose", _fake_diagnose)
        monkeypatch.setattr(devices_module.QMessageBox, "warning", lambda *args, **kwargs: None)

        dialog = devices_module.NetworkDiagnosticDialog()
        qtbot.addWidget(dialog)
        dialog.ip_edit.setText("")
        dialog.run_button.click()

        assert called is False
        assert dialog.open_folder_button.isEnabled() is False

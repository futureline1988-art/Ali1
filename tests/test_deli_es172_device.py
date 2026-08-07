"""Tests for devices/deli_es172_device.py -- the client for the DELI
ES172's discovered HTTP control API (POST /control?api_key=...,
GetVersionInfo command), confirmed against the real device.

Uses local throwaway HTTP servers rather than the real device (not
reachable from this environment); each test proves one classification
this client must get right (success, wrong API key, timeout, network
error, invalid JSON, HTTP error) plus that the API key is never leaked
into a stored/displayed error string.
"""

from __future__ import annotations

import http.server
import json
import threading
import time

import pytest

from devices import deli_es172_device as deli


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    """Serves a configurable response and records the received request."""

    mode = "success"
    received_requests: list[dict] = []
    delay_seconds = 0.0

    def log_message(self, format, *args):  # noqa: A002
        pass

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(content_length) if content_length else b""
        try:
            body_json = json.loads(body_raw) if body_raw else None
        except json.JSONDecodeError:
            body_json = None
        type(self).received_requests.append(
            {
                "path": self.path,
                "headers": dict(self.headers),
                "body": body_json,
            }
        )

        if self.delay_seconds:
            time.sleep(self.delay_seconds)

        if self.mode == "success":
            payload = json.dumps(
                {"mid": "1", "cmd": "GetVersionInfo", "result": {"firmware": "3.16.1019"}}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif self.mode == "auth_error_401":
            payload = b"Unauthorized"
            self.send_response(401)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif self.mode == "auth_error_403":
            payload = b"Forbidden"
            self.send_response(403)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif self.mode == "invalid_json":
            payload = b"<html>not json</html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif self.mode == "http_error_with_json":
            payload = json.dumps({"error": "internal"}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            raise AssertionError(f"unknown test mode: {self.mode}")


@pytest.fixture
def recording_server():
    _RecordingHandler.received_requests = []
    _RecordingHandler.mode = "success"
    _RecordingHandler.delay_seconds = 0.0
    server = http.server.HTTPServer(("127.0.0.1", 0), _RecordingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port
    server.shutdown()


@pytest.fixture
def closed_port():
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestSuccessResponse:
    def test_success_is_classified_and_parsed(self, recording_server):
        result = deli.test_get_version_info(f"127.0.0.1:{recording_server}", "secret-key")
        assert result.outcome == deli.OUTCOME_SUCCESS
        assert result.http_status == 200
        assert result.parsed_response["result"]["firmware"] == "3.16.1019"
        assert result.raw_response_text is not None

    def test_request_matches_the_confirmed_contract(self, recording_server):
        deli.test_get_version_info(f"127.0.0.1:{recording_server}", "secret-key")
        assert len(_RecordingHandler.received_requests) == 1
        request = _RecordingHandler.received_requests[0]
        assert request["path"] == "/control?api_key=secret-key"
        assert request["headers"]["Content-Type"] == "application/json"
        assert request["body"] == {"mid": "1", "cmd": "GetVersionInfo", "payload": {}}


class TestAuthErrors:
    def test_401_is_auth_error(self, recording_server):
        _RecordingHandler.mode = "auth_error_401"
        result = deli.test_get_version_info(f"127.0.0.1:{recording_server}", "wrong-key")
        assert result.outcome == deli.OUTCOME_AUTH_ERROR
        assert result.http_status == 401

    def test_403_is_auth_error(self, recording_server):
        _RecordingHandler.mode = "auth_error_403"
        result = deli.test_get_version_info(f"127.0.0.1:{recording_server}", "wrong-key")
        assert result.outcome == deli.OUTCOME_AUTH_ERROR
        assert result.http_status == 403


class TestInvalidJson:
    def test_non_json_body_is_classified_as_invalid_json(self, recording_server):
        _RecordingHandler.mode = "invalid_json"
        result = deli.test_get_version_info(f"127.0.0.1:{recording_server}", "secret-key")
        assert result.outcome == deli.OUTCOME_INVALID_JSON
        assert result.raw_response_text == "<html>not json</html>"
        assert result.parsed_response is None


class TestHttpError:
    def test_server_error_with_valid_json_is_http_error(self, recording_server):
        _RecordingHandler.mode = "http_error_with_json"
        result = deli.test_get_version_info(f"127.0.0.1:{recording_server}", "secret-key")
        assert result.outcome == deli.OUTCOME_HTTP_ERROR
        assert result.http_status == 500
        assert result.parsed_response == {"error": "internal"}


class TestNetworkErrorsAndRedaction:
    def test_closed_port_is_network_error(self, closed_port):
        result = deli.test_get_version_info(f"127.0.0.1:{closed_port}", "super-secret-key")
        assert result.outcome == deli.OUTCOME_NETWORK_ERROR
        assert result.parsed_response is None

    def test_api_key_never_appears_in_error_detail(self, closed_port):
        result = deli.test_get_version_info(f"127.0.0.1:{closed_port}", "super-secret-key")
        assert result.error_detail is not None
        assert "super-secret-key" not in result.error_detail
        assert "***" in result.error_detail

    def test_timeout_is_classified(self, recording_server):
        _RecordingHandler.delay_seconds = 1.0
        result = deli.test_get_version_info(
            f"127.0.0.1:{recording_server}", "secret-key", timeout=0.1
        )
        assert result.outcome == deli.OUTCOME_TIMEOUT


class TestRedactApiKeyHelper:
    def test_redacts_every_occurrence(self):
        text = "error at http://x/control?api_key=abc123 (abc123 rejected)"
        redacted = deli._redact_api_key(text, "abc123")
        assert "abc123" not in redacted
        assert redacted.count("***") == 2

    def test_empty_api_key_is_a_no_op(self):
        text = "no key here"
        assert deli._redact_api_key(text, "") == text


class TestDeliConnectionTestDialog:
    """ui.devices.DeliConnectionTestDialog -- the in-app "اختبار اتصال DELI"
    screen, wired to devices.deli_es172_device.test_get_version_info."""

    def test_default_ip_is_prefilled_and_key_field_is_masked(self, qtbot):
        from PySide6.QtWidgets import QLineEdit

        from tools.diagnostics import deli_es172_diagnose as network_diagnostic
        from ui.devices import DeliConnectionTestDialog

        dialog = DeliConnectionTestDialog()
        qtbot.addWidget(dialog)
        assert dialog.ip_edit.text() == network_diagnostic.DEFAULT_TARGET_IP
        assert dialog.api_key_edit.echoMode() == QLineEdit.EchoMode.Password

    def test_successful_response_is_shown_parsed_and_raw(self, qtbot, monkeypatch):
        from ui import devices as devices_module

        fake_result = deli.DeliVersionInfoResult(
            outcome=deli.OUTCOME_SUCCESS,
            message_ar="تم الاتصال بنجاح.",
            http_status=200,
            raw_response_text='{"result": {"firmware": "3.16.1019"}}',
            parsed_response={"result": {"firmware": "3.16.1019"}},
        )
        monkeypatch.setattr(
            devices_module.deli_es172_device, "test_get_version_info", lambda ip, key: fake_result
        )

        dialog = devices_module.DeliConnectionTestDialog()
        qtbot.addWidget(dialog)
        dialog.ip_edit.setText("192.168.1.8")
        dialog.api_key_edit.setText("secret-key")
        dialog.run_button.click()

        assert dialog.status_label.text() == "تم الاتصال بنجاح."
        assert "3.16.1019" in dialog.parsed_view.toPlainText()
        assert "3.16.1019" in dialog.raw_view.toPlainText()

    def test_auth_error_is_shown(self, qtbot, monkeypatch):
        from ui import devices as devices_module

        fake_result = deli.DeliVersionInfoResult(
            outcome=deli.OUTCOME_AUTH_ERROR,
            message_ar="مفتاح API غير صحيح أو مرفوض من الجهاز (رمز الاستجابة 401).",
            http_status=401,
            raw_response_text="Unauthorized",
        )
        monkeypatch.setattr(
            devices_module.deli_es172_device, "test_get_version_info", lambda ip, key: fake_result
        )

        dialog = devices_module.DeliConnectionTestDialog()
        qtbot.addWidget(dialog)
        dialog.ip_edit.setText("192.168.1.8")
        dialog.api_key_edit.setText("wrong-key")
        dialog.run_button.click()

        assert "401" in dialog.status_label.text()
        assert dialog.parsed_view.toPlainText() == "—"

    def test_empty_fields_are_rejected_before_running_anything(self, qtbot, monkeypatch):
        from ui import devices as devices_module

        called = False

        def _fake(ip, key):
            nonlocal called
            called = True
            return None

        monkeypatch.setattr(devices_module.deli_es172_device, "test_get_version_info", _fake)
        monkeypatch.setattr(devices_module.QMessageBox, "warning", lambda *a, **k: None)

        dialog = devices_module.DeliConnectionTestDialog()
        qtbot.addWidget(dialog)
        dialog.ip_edit.setText("")
        dialog.api_key_edit.setText("")
        dialog.run_button.click()

        assert called is False

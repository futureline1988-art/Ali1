"""DeviceService.sync_attendance_logs: duplicate device punches are never counted twice.

Uses a real, scripted DELI ES172 HTTP server (same pattern as
``tests/test_deli_es172_connector.py``) so this proves the real
``DeviceManager`` -> ``DeliES172Connector`` -> ``DeviceService`` chain
dedups on ``(employee, punch_time)``, not a mocked shortcut -- directly
covering the product requirement "prevent duplicate punches; never
count the same device attendance record twice."
"""

from __future__ import annotations

import http.server
import json
import threading

import pytest

from database.database import session_scope
from models.enums import DeviceProtocol
from repositories.attendance_repository import AttendancePunchRepository
from services.device_service import DeviceService
from services.employee_service import EmployeeService


class _ScriptedHandler(http.server.BaseHTTPRequestHandler):
    responses: dict[str, object] = {}

    def log_message(self, format, *args):  # noqa: A002
        pass

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(content_length) if content_length else b""
        body = json.loads(body_raw) if body_raw else {}
        envelope = type(self).responses.get(
            body.get("cmd"), {"mid": body.get("mid"), "result": "Success", "payload": {}}
        )
        payload = json.dumps(envelope).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def scripted_server():
    _ScriptedHandler.responses = {}
    server = http.server.HTTPServer(("127.0.0.1", 0), _ScriptedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port
    server.shutdown()


def test_syncing_the_same_device_log_twice_creates_only_one_punch(company_factory, scripted_server):
    company_id = company_factory()
    with session_scope() as session:
        EmployeeService(session, company_id=company_id).create_employee(
            employee_number="9001", full_name="سامر فؤاد"
        )
        device = DeviceService(session, company_id=company_id).create_device(
            name="DELI مزامنة", protocol=DeviceProtocol.DELI_ES172, host="127.0.0.1", port=scripted_server
        )
        device_id = device.id

    _ScriptedHandler.responses = {
        "GetAttendLog": {
            "mid": "1",
            "result": "AttendLog",
            "payload": {"logs": [{"user_id": "9001", "time": "2026-09-08T08:00:00", "mode": "face"}]},
        },
    }

    with session_scope() as session:
        service = DeviceService(session, company_id=company_id)
        device = service.device_repo.get_by_id(device_id)
        first_created = service.sync_attendance_logs(device)
    assert len(first_created) == 1

    # Re-sync the exact same device log a second time (e.g. a scheduled
    # job running again before the device rotates its buffer).
    with session_scope() as session:
        service = DeviceService(session, company_id=company_id)
        device = service.device_repo.get_by_id(device_id)
        second_created = service.sync_attendance_logs(device)
    assert second_created == []  # nothing new -- already imported

    with session_scope() as session:
        punch_repo = AttendancePunchRepository(session, company_id=company_id)
        from datetime import datetime, timezone

        all_punches = punch_repo.list_for_device_between(
            device_id,
            datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 8, 23, 59, tzinfo=timezone.utc),
        )
    assert len(all_punches) == 1  # never double-counted

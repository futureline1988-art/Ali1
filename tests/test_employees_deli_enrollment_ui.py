"""Employees screen: DELI ES172 biometric-enrollment button wiring.

Proves "تسجيل بصمة الوجه" (and the DELI-only fingerprint/palm buttons)
are not decorative: selecting an employee, picking a DELI ES172
device, and clicking the button must call the real
``DeviceController.begin_deli_enrollment`` -> ``DeviceService`` ->
``DeliES172Connector`` chain against a scripted HTTP server speaking
the documented protocol, open :class:`~ui.deli_enrollment_dialog.DeliEnrollmentDialog`
with the resulting job, and refresh the employee list once the dialog
reports the template was attached.

The dialog's own internal background-thread polling is exercised
separately by the connector/service tests
(``tests/test_deli_es172_connector.py``,
``tests/test_device_service_deli_enrollment.py``); here it is swapped
for a lightweight stand-in so these tests stay deterministic and don't
depend on Qt's cross-thread signal-delivery timing.
"""

from __future__ import annotations

import http.server
import json
import threading
from typing import Any

import pytest
from PySide6.QtWidgets import QDialog

from database.database import session_scope
from models.enums import DeviceProtocol
from services.device_service import DeviceService
from services.employee_service import EmployeeService
from ui.employees import EmployeesPage
from ui.face_enrollment_dialog import SelectDeviceDialog


class _ScriptedHandler(http.server.BaseHTTPRequestHandler):
    responses: dict[str, object] = {}
    received_requests: list[dict] = []

    def log_message(self, format, *args):  # noqa: A002
        pass

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(content_length) if content_length else b""
        body = json.loads(body_raw) if body_raw else {}
        type(self).received_requests.append({"path": self.path, "body": body})

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
    _ScriptedHandler.received_requests = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _ScriptedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port
    server.shutdown()


@pytest.fixture
def deli_employees_setup(company_factory, scripted_server):
    company_id = company_factory()
    with session_scope() as session:
        device = DeviceService(session, company_id=company_id).create_device(
            name="DELI ES172", protocol=DeviceProtocol.DELI_ES172, host="127.0.0.1", port=scripted_server
        )
        employee = EmployeeService(session, company_id=company_id).create_employee(
            employee_number="8001", full_name="نور حسين"
        )
        return company_id, device.id, employee.id


def _select_first_device_exec(self) -> int:
    self.device_combo.setCurrentIndex(0)
    return QDialog.Accepted


class _FakeDeliEnrollmentDialog:
    """Stand-in for :class:`~ui.deli_enrollment_dialog.DeliEnrollmentDialog`.

    Records the constructor kwargs ``ui.employees`` passed in (proving
    the real begin-enrollment result reached the dialog) and returns a
    canned outcome instead of driving a real background poll thread.
    """

    last_kwargs: dict[str, Any] | None = None
    canned_final_result: dict[str, Any] | None = {
        "outcome": "attached",
        "message_ar": "تم حفظ بصمة الوجه للموظف بنجاح.",
    }

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_kwargs = kwargs

    def exec(self) -> int:
        return QDialog.Accepted

    def final_result(self) -> dict[str, Any] | None:
        return type(self).canned_final_result


@pytest.fixture(autouse=True)
def _reset_fake_dialog():
    _FakeDeliEnrollmentDialog.last_kwargs = None
    _FakeDeliEnrollmentDialog.canned_final_result = {
        "outcome": "attached",
        "message_ar": "تم حفظ بصمة الوجه للموظف بنجاح.",
    }
    yield


def _full_permissions() -> frozenset[str]:
    return frozenset({"employees.view", "employees.manage", "devices.view", "devices.manage"})


class TestDeliButtonsSelectionGating:
    def test_buttons_disabled_without_selection(self, qtbot, company_factory):
        company_id = company_factory()
        page = EmployeesPage(company_id=company_id, permission_codes=_full_permissions())
        qtbot.addWidget(page)

        assert page.register_face_button.isEnabled() is False
        assert page.register_fingerprint_deli_button.isEnabled() is False
        assert page.register_palm_deli_button.isEnabled() is False

    def test_buttons_enabled_after_selecting_a_row(self, qtbot, deli_employees_setup):
        company_id, _device_id, _employee_id = deli_employees_setup
        page = EmployeesPage(company_id=company_id, permission_codes=_full_permissions())
        qtbot.addWidget(page)
        page.refresh()
        page.table.selectRow(0)

        assert page.register_face_button.isEnabled() is True
        assert page.register_fingerprint_deli_button.isEnabled() is True
        assert page.register_palm_deli_button.isEnabled() is True


class TestRegisterFaceOnDeliDevice:
    def test_starts_deli_workflow_opens_dialog_and_refreshes_on_attached(
        self, qtbot, monkeypatch, deli_employees_setup
    ):
        company_id, device_id, employee_id = deli_employees_setup
        _ScriptedHandler.responses = {
            "GetDeviceCapabilities": {
                "mid": "1",
                "result": "DeviceCapabilities",
                "payload": {"face": True},
            },
            "BeginEnrollFace": {"mid": "1", "result": "JobCreated", "payload": {"job_id": 3}},
        }

        page = EmployeesPage(company_id=company_id, permission_codes=_full_permissions())
        qtbot.addWidget(page)
        page.refresh()
        page.table.selectRow(0)

        monkeypatch.setattr(SelectDeviceDialog, "exec", _select_first_device_exec)
        monkeypatch.setattr("ui.employees.DeliEnrollmentDialog", _FakeDeliEnrollmentDialog)
        monkeypatch.setattr("ui.employees.QMessageBox.information", lambda *a, **k: None)

        page._on_register_face_clicked()

        assert _FakeDeliEnrollmentDialog.last_kwargs is not None
        assert _FakeDeliEnrollmentDialog.last_kwargs["device_id"] == device_id
        assert _FakeDeliEnrollmentDialog.last_kwargs["employee_id"] == employee_id
        begin_result = _FakeDeliEnrollmentDialog.last_kwargs["begin_result"]
        assert begin_result["outcome"] == "started"
        assert begin_result["job"]["kind"] == "face"
        assert begin_result["job"]["job_id"] == 3

        commands_sent = [r["body"]["cmd"] for r in _ScriptedHandler.received_requests]
        assert "SetUserInfo" in commands_sent  # employee pushed before capture began
        assert "BeginEnrollFace" in commands_sent

    def test_unsupported_device_shows_error_without_opening_dialog(
        self, qtbot, monkeypatch, deli_employees_setup
    ):
        company_id, _device_id, _employee_id = deli_employees_setup
        _ScriptedHandler.responses = {
            "GetDeviceCapabilities": {
                "mid": "1",
                "result": "DeviceCapabilities",
                "payload": {"face": False},
            },
        }

        page = EmployeesPage(company_id=company_id, permission_codes=_full_permissions())
        qtbot.addWidget(page)
        page.refresh()
        page.table.selectRow(0)

        monkeypatch.setattr(SelectDeviceDialog, "exec", _select_first_device_exec)
        monkeypatch.setattr("ui.employees.DeliEnrollmentDialog", _FakeDeliEnrollmentDialog)

        page._on_register_face_clicked()

        assert _FakeDeliEnrollmentDialog.last_kwargs is None  # dialog never opened
        assert "لا يدعم" in page.error_label.text()


class TestRegisterFingerprintAndPalmOnDeliDevice:
    def test_fingerprint_button_starts_fingerprint_kind_job(
        self, qtbot, monkeypatch, deli_employees_setup
    ):
        company_id, device_id, employee_id = deli_employees_setup
        _ScriptedHandler.responses = {
            "GetDeviceCapabilities": {
                "mid": "1",
                "result": "DeviceCapabilities",
                "payload": {"fingerprint": True},
            },
            "BeginEnrollFp": {"mid": "1", "result": "JobCreated", "payload": {"job_id": 11}},
        }
        page = EmployeesPage(company_id=company_id, permission_codes=_full_permissions())
        qtbot.addWidget(page)
        page.refresh()
        page.table.selectRow(0)

        monkeypatch.setattr(SelectDeviceDialog, "exec", _select_first_device_exec)
        monkeypatch.setattr("ui.employees.DeliEnrollmentDialog", _FakeDeliEnrollmentDialog)
        monkeypatch.setattr("ui.employees.QMessageBox.information", lambda *a, **k: None)

        page._on_register_fingerprint_deli_clicked()

        assert _FakeDeliEnrollmentDialog.last_kwargs["begin_result"]["job"]["kind"] == "fp"

    def test_palm_button_starts_palm_kind_job(self, qtbot, monkeypatch, deli_employees_setup):
        company_id, device_id, employee_id = deli_employees_setup
        _ScriptedHandler.responses = {
            "GetDeviceCapabilities": {
                "mid": "1",
                "result": "DeviceCapabilities",
                "payload": {"palm": True},
            },
            "BeginEnrollPalm": {"mid": "1", "result": "JobCreated", "payload": {"job_id": 12}},
        }
        page = EmployeesPage(company_id=company_id, permission_codes=_full_permissions())
        qtbot.addWidget(page)
        page.refresh()
        page.table.selectRow(0)

        monkeypatch.setattr(SelectDeviceDialog, "exec", _select_first_device_exec)
        monkeypatch.setattr("ui.employees.DeliEnrollmentDialog", _FakeDeliEnrollmentDialog)
        monkeypatch.setattr("ui.employees.QMessageBox.information", lambda *a, **k: None)

        page._on_register_palm_deli_clicked()

        assert _FakeDeliEnrollmentDialog.last_kwargs["begin_result"]["job"]["kind"] == "palm"

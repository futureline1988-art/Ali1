"""Tests for DeviceService's/DeviceController's DELI ES172 on-device
biometric-enrollment workflow (begin -> poll -> confirm/cancel).

Uses a real, scripted throwaway HTTP server speaking the documented
DELI response envelope (see ``tests/test_deli_es172_connector.py``) so
these tests exercise the real ``DeviceManager`` -> ``DeliES172Connector``
path end to end, not a mocked service -- proving the actual documented
commands (``BeginEnrollFace``/``QueryJobStatus``/``SetUserInfo``/
``CancelJob``) are sent with the right payloads at each step.
"""

from __future__ import annotations

import http.server
import json
import threading

import pytest

from controllers.device_controller import DeviceController
from database.database import session_scope
from devices.deli_es172_device import ENROLLMENT_KIND_FACE, ENROLLMENT_KIND_PALM
from models.enums import DeviceProtocol
from services.device_service import DeliEnrollmentOutcome, DeviceService
from services.employee_service import EmployeeService


class _ScriptedHandler(http.server.BaseHTTPRequestHandler):
    responses: dict[str, object] = {}
    received_requests: list[dict] = []
    call_counts: dict[str, int] = {}

    def log_message(self, format, *args):  # noqa: A002
        pass

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(content_length) if content_length else b""
        body = json.loads(body_raw) if body_raw else {}
        type(self).received_requests.append({"path": self.path, "body": body})

        cmd = body.get("cmd")
        counts = type(self).call_counts
        counts[cmd] = counts.get(cmd, 0) + 1

        scripted = type(self).responses.get(cmd)
        if scripted is None:
            envelope = {"mid": body.get("mid"), "result": "Success", "payload": {}}
        elif isinstance(scripted, list):
            index = min(counts[cmd] - 1, len(scripted) - 1)
            envelope = scripted[index]
        else:
            envelope = scripted

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
    _ScriptedHandler.call_counts = {}
    server = http.server.HTTPServer(("127.0.0.1", 0), _ScriptedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port
    server.shutdown()


@pytest.fixture
def deli_setup(company_factory, scripted_server):
    company_id = company_factory()
    with session_scope() as session:
        device = DeviceService(session, company_id=company_id).create_device(
            name="DELI ES172 اختبار",
            protocol=DeviceProtocol.DELI_ES172,
            host="127.0.0.1",
            port=scripted_server,
        )
        employee = EmployeeService(session, company_id=company_id).create_employee(
            employee_number="7001", full_name="ياسمين علي"
        )
        return company_id, device.id, employee.id


class TestBeginDeliEnrollment:
    def test_unsupported_device_when_not_deli_protocol(self, company_factory):
        company_id = company_factory()
        with session_scope() as session:
            device = DeviceService(session, company_id=company_id).create_device(
                name="جهاز آخر",
                protocol=DeviceProtocol.ZKTECO_TCP,
                host="10.0.0.5",
                port=4370,
            )
            employee = EmployeeService(session, company_id=company_id).create_employee(
                employee_number="1001", full_name="Test"
            )
            service = DeviceService(session, company_id=company_id)
            result = service.begin_deli_enrollment(device, employee, kind=ENROLLMENT_KIND_FACE)

        assert result.outcome is DeliEnrollmentOutcome.UNSUPPORTED_DEVICE

    def test_unsupported_when_device_does_not_report_face_capability(self, deli_setup):
        company_id, device_id, employee_id = deli_setup
        _ScriptedHandler.responses = {
            "GetDeviceCapabilities": {
                "mid": "1",
                "result": "DeviceCapabilities",
                "payload": {"face": False},
            },
        }
        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            employee = service.employee_repo.get_by_id(employee_id)
            result = service.begin_deli_enrollment(device, employee, kind=ENROLLMENT_KIND_FACE)

        assert result.outcome is DeliEnrollmentOutcome.UNSUPPORTED_DEVICE

    def test_starts_successfully_pushes_employee_then_begins_enroll_face(self, deli_setup):
        company_id, device_id, employee_id = deli_setup
        _ScriptedHandler.responses = {
            "GetDeviceCapabilities": {
                "mid": "1",
                "result": "DeviceCapabilities",
                "payload": {"face": True},
            },
            "BeginEnrollFace": {"mid": "1", "result": "JobCreated", "payload": {"job_id": 5}},
        }
        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            employee = service.employee_repo.get_by_id(employee_id)
            result = service.begin_deli_enrollment(device, employee, kind=ENROLLMENT_KIND_FACE)

        assert result.outcome is DeliEnrollmentOutcome.STARTED
        assert result.job.job_id == 5
        assert result.job.kind == ENROLLMENT_KIND_FACE
        commands_sent = [r["body"]["cmd"] for r in _ScriptedHandler.received_requests]
        assert "SetUserInfo" in commands_sent
        assert commands_sent.index("SetUserInfo") < commands_sent.index("BeginEnrollFace")
        push_request = next(r for r in _ScriptedHandler.received_requests if r["body"]["cmd"] == "SetUserInfo")
        assert push_request["body"]["payload"] == {"id": "7001", "name": "ياسمين علي"}


class TestPollDeliEnrollmentJob:
    def test_pending_state_reported_without_error(self, deli_setup):
        company_id, device_id, _employee_id = deli_setup
        from devices.deli_es172_device import JOB_STATE_PENDING, DeliEnrollmentJob

        _ScriptedHandler.responses = {
            "QueryJobStatus": {
                "mid": "1",
                "result": "JobStatus",
                "payload": {"job_id": 5, "state": "pending"},
            },
        }
        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            job = DeliEnrollmentJob(job_id=5, kind=ENROLLMENT_KIND_FACE, state=JOB_STATE_PENDING)
            result = service.poll_deli_enrollment_job(device, job)

        assert result.outcome is DeliEnrollmentOutcome.PENDING

    def test_succeeded_state_carries_template_data(self, deli_setup):
        company_id, device_id, _employee_id = deli_setup
        from devices.deli_es172_device import JOB_STATE_PENDING, DeliEnrollmentJob

        _ScriptedHandler.responses = {
            "QueryJobStatus": {
                "mid": "1",
                "result": "JobStatus",
                "payload": {"job_id": 5, "state": "succeeded", "face_data": "b64face"},
            },
        }
        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            job = DeliEnrollmentJob(job_id=5, kind=ENROLLMENT_KIND_FACE, state=JOB_STATE_PENDING)
            result = service.poll_deli_enrollment_job(device, job)

        assert result.outcome is DeliEnrollmentOutcome.SUCCEEDED
        assert result.job.template_data == "b64face"

    def test_failed_state_reported(self, deli_setup):
        company_id, device_id, _employee_id = deli_setup
        from devices.deli_es172_device import JOB_STATE_PENDING, DeliEnrollmentJob

        _ScriptedHandler.responses = {
            "QueryJobStatus": {
                "mid": "1",
                "result": "JobStatus",
                "payload": {"job_id": 5, "state": "failed"},
            },
        }
        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            job = DeliEnrollmentJob(job_id=5, kind=ENROLLMENT_KIND_FACE, state=JOB_STATE_PENDING)
            result = service.poll_deli_enrollment_job(device, job)

        assert result.outcome is DeliEnrollmentOutcome.FAILED


class TestConfirmDeliEnrollment:
    def test_rejects_confirmation_when_job_not_succeeded(self, deli_setup):
        from devices.deli_es172_device import JOB_STATE_PENDING, DeliEnrollmentJob

        company_id, device_id, employee_id = deli_setup
        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            employee = service.employee_repo.get_by_id(employee_id)
            job = DeliEnrollmentJob(job_id=5, kind=ENROLLMENT_KIND_FACE, state=JOB_STATE_PENDING)
            result = service.confirm_deli_enrollment(device, employee, job)

        assert result.outcome is DeliEnrollmentOutcome.FAILED

    def test_attaches_face_template_and_updates_employee_record(self, deli_setup):
        from devices.deli_es172_device import JOB_STATE_SUCCEEDED, DeliEnrollmentJob

        company_id, device_id, employee_id = deli_setup
        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            employee = service.employee_repo.get_by_id(employee_id)
            job = DeliEnrollmentJob(
                job_id=5, kind=ENROLLMENT_KIND_FACE, state=JOB_STATE_SUCCEEDED, template_data="b64face"
            )
            result = service.confirm_deli_enrollment(device, employee, job)

        assert result.outcome is DeliEnrollmentOutcome.ATTACHED
        request = _ScriptedHandler.received_requests[-1]
        assert request["body"]["cmd"] == "SetUserInfo"
        assert request["body"]["payload"] == {"id": "7001", "face": "b64face"}

        with session_scope() as session:
            employee = DeviceService(session, company_id=company_id).employee_repo.get_by_id(employee_id)
            assert employee.face_enrolled is True
            assert employee.face_enrolled_device_id == device_id

    def test_attaches_palm_template_as_array_and_sets_palm_registered(self, deli_setup):
        from devices.deli_es172_device import JOB_STATE_SUCCEEDED, DeliEnrollmentJob

        company_id, device_id, employee_id = deli_setup
        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            employee = service.employee_repo.get_by_id(employee_id)
            job = DeliEnrollmentJob(
                job_id=6, kind=ENROLLMENT_KIND_PALM, state=JOB_STATE_SUCCEEDED, template_data="b64palm"
            )
            result = service.confirm_deli_enrollment(device, employee, job)

        assert result.outcome is DeliEnrollmentOutcome.ATTACHED
        request = _ScriptedHandler.received_requests[-1]
        assert request["body"]["payload"] == {"id": "7001", "palm": ["b64palm"]}

        with session_scope() as session:
            employee = DeviceService(session, company_id=company_id).employee_repo.get_by_id(employee_id)
            assert employee.palm_registered is True


class TestCancelDeliEnrollment:
    def test_sends_cancel_job_and_reports_cancelled(self, deli_setup):
        from devices.deli_es172_device import JOB_STATE_PENDING, DeliEnrollmentJob

        company_id, device_id, _employee_id = deli_setup
        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            job = DeliEnrollmentJob(job_id=5, kind=ENROLLMENT_KIND_FACE, state=JOB_STATE_PENDING)
            result = service.cancel_deli_enrollment(device, job)

        assert result.outcome is DeliEnrollmentOutcome.CANCELLED
        request = _ScriptedHandler.received_requests[-1]
        assert request["body"]["cmd"] == "CancelJob"
        assert request["body"]["payload"] == {"job_id": 5}


class TestDeviceControllerWiring:
    """Proves the same workflow through the real controller (UI -> controller
    -> service -> connector chain), including RBAC.
    """

    def test_full_workflow_via_controller(self, deli_setup):
        company_id, device_id, employee_id = deli_setup
        _ScriptedHandler.responses = {
            "GetDeviceCapabilities": {
                "mid": "1",
                "result": "DeviceCapabilities",
                "payload": {"face": True},
            },
            "BeginEnrollFace": {"mid": "1", "result": "JobCreated", "payload": {"job_id": 9}},
            "QueryJobStatus": {
                "mid": "1",
                "result": "JobStatus",
                "payload": {"job_id": 9, "state": "succeeded", "face_data": "b64face"},
            },
        }
        controller = DeviceController(
            company_id=company_id,
            actor_user_id=None,
            permission_codes=frozenset({"devices.view", "devices.manage"}),
        )

        begin_result = controller.begin_deli_enrollment(
            device_id=device_id, employee_id=employee_id, kind=ENROLLMENT_KIND_FACE
        )
        assert begin_result["outcome"] == "started"
        job = begin_result["job"]
        assert job["job_id"] == 9

        poll_result = controller.poll_deli_enrollment_job(device_id=device_id, job=job)
        assert poll_result["outcome"] == "succeeded"

        confirm_result = controller.confirm_deli_enrollment(
            device_id=device_id, employee_id=employee_id, job=poll_result["job"]
        )
        assert confirm_result["outcome"] == "attached"

        status = controller.get_employee_biometric_status(employee_id)
        assert status["face_enrolled"] is True

    def test_begin_deli_enrollment_denied_without_permission(self, deli_setup):
        company_id, device_id, employee_id = deli_setup
        controller = DeviceController(
            company_id=company_id, actor_user_id=None, permission_codes=frozenset({"devices.view"})
        )
        result = controller.begin_deli_enrollment(
            device_id=device_id, employee_id=employee_id, kind=ENROLLMENT_KIND_FACE
        )
        assert result is None

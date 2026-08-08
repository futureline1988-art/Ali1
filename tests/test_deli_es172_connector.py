"""Tests for devices.deli_es172_device.DeliES172Connector -- the real
DeviceConnector implementation for the DELI ES172, built directly from
the official "DELI offline SDK protocol" document (JSON-over-HTTP
control interface, GetVersionInfo/GetDeviceUid/GetDeviceCapabilities/
GetCapacityLimit/GetCurrentUsage/GetUserIdList/GetUserInfo/GetAttendLog/
SetUserInfo).

Uses a local, throwaway HTTP server that speaks the documented response
envelope (``{"mid", "result", "payload"}``) so every test exercises the
real request/response contract, not a guessed one.
"""

from __future__ import annotations

import http.server
import json
import threading

import pytest

from devices.device_interface import (
    DeviceConnectionError,
    RawDeviceUser,
)
from devices.deli_es172_device import (
    ENROLLMENT_KIND_FACE,
    ENROLLMENT_KIND_FINGERPRINT,
    ENROLLMENT_KIND_PALM,
    JOB_STATE_FAILED,
    JOB_STATE_PENDING,
    JOB_STATE_SUCCEEDED,
    DeliCommandError,
    DeliEnrollmentJob,
    DeliES172Connector,
)
from models.enums import PunchType


class _ScriptedHandler(http.server.BaseHTTPRequestHandler):
    """Replies to each DELI ``cmd`` using a per-test scripted response map."""

    #: cmd -> either a single response dict, or a list of response dicts
    #: consumed one per call (for pagination sequences).
    responses: dict[str, object] = {}
    received_requests: list[dict] = []
    call_counts: dict[str, int] = {}
    #: When set, every request gets this raw HTTP status with an empty
    #: body instead of a scripted JSON envelope (for auth-rejection tests).
    force_status: int | None = None

    def log_message(self, format, *args):  # noqa: A002
        pass

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body_raw = self.rfile.read(content_length) if content_length else b""
        body = json.loads(body_raw) if body_raw else {}
        type(self).received_requests.append({"path": self.path, "body": body})

        if type(self).force_status is not None:
            self.send_response(type(self).force_status)
            self.end_headers()
            return

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


@pytest.fixture(autouse=True)
def _reset_force_status():
    _ScriptedHandler.force_status = None
    yield
    _ScriptedHandler.force_status = None


def _connector(port: int, *, api_key: str | None = "test-key") -> DeliES172Connector:
    return DeliES172Connector(host="127.0.0.1", port=port, api_key=api_key, timeout=5.0)


class TestGetVersionInfoAndConnectionTest:
    def test_connection_succeeds_and_reads_capabilities(self, scripted_server):
        _ScriptedHandler.responses = {
            "GetVersionInfo": {
                "mid": "1",
                "result": "VersionInfo",
                "payload": {"firmware_version": "13750C v3.11.802"},
            },
            "GetDeviceCapabilities": {
                "mid": "1",
                "result": "DeviceCapabilities",
                "payload": {"face": True, "fingerprint": True},
            },
            "GetCapacityLimit": {
                "mid": "1",
                "result": "CapacityLimit",
                "payload": {"max_user": 5000, "max_face": 3000, "max_fp": 15000},
            },
            "GetCurrentUsage": {
                "mid": "1",
                "result": "CurrentUsage",
                "payload": {"user_count": 1, "face_count": 1, "fp_count": 1, "attend_log_count": 26},
            },
        }
        connector = _connector(scripted_server)
        result = connector.test_connection_detailed()
        assert result.success is True
        assert result.capabilities.supports_face is True
        assert result.capabilities.supports_fingerprint is True
        assert result.capabilities.supports_palm is False
        assert result.capabilities.user_count == 1
        assert result.capabilities.user_capacity == 5000
        assert result.capabilities.firmware_version == "13750C v3.11.802"
        assert connector.test_connection() is True

    def test_request_body_matches_the_documented_contract(self, scripted_server):
        connector = _connector(scripted_server, api_key="secret")
        connector._call("GetVersionInfo")
        request = _ScriptedHandler.received_requests[0]
        assert request["path"] == "/control?api_key=secret"
        assert request["body"] == {"mid": "1", "cmd": "GetVersionInfo", "payload": {}}

    def test_blank_api_key_is_sent_as_empty_query_param(self, scripted_server):
        connector = _connector(scripted_server, api_key=None)
        connector._call("GetVersionInfo")
        request = _ScriptedHandler.received_requests[0]
        assert request["path"] == "/control?api_key="

    def test_auth_rejection_is_reported_without_raising_an_unrelated_error(self, scripted_server):
        _ScriptedHandler.force_status = 401
        connector = _connector(scripted_server)
        with pytest.raises(DeviceConnectionError):
            connector._call("GetVersionInfo")

    def test_device_level_error_result_raises_deli_command_error_with_code(self, scripted_server):
        _ScriptedHandler.responses = {
            "GetUserInfo": {
                "mid": "1",
                "result": "Error",
                "payload": {"code": "user_id_not_exists", "arguments": [12]},
            }
        }
        connector = _connector(scripted_server)
        with pytest.raises(DeliCommandError) as excinfo:
            connector._call("GetUserInfo", {"id": "12"})
        assert excinfo.value.code == "user_id_not_exists"

    def test_connection_test_reports_command_success_but_capabilities_failure(self, scripted_server):
        _ScriptedHandler.responses = {
            "GetVersionInfo": {"mid": "1", "result": "VersionInfo", "payload": {}},
            "GetDeviceCapabilities": {
                "mid": "1",
                "result": "Error",
                "payload": {"code": "internal_error"},
            },
        }
        connector = _connector(scripted_server)
        result = connector.test_connection_detailed()
        assert result.success is True
        assert result.capabilities is None
        assert result.detail


class TestUserIdListPagination:
    def test_follows_next_page_pos_until_absent(self, scripted_server):
        _ScriptedHandler.responses = {
            "GetUserIdList": [
                {"mid": "1", "result": "UserIdList", "payload": {"user_id": ["1", "5"], "next_page_pos": 50}},
                {"mid": "2", "result": "UserIdList", "payload": {"user_id": ["121"]}},
            ],
            "GetUserInfo": {"mid": "1", "result": "UserInfo", "payload": {"id": "x", "name": "Someone"}},
        }
        connector = _connector(scripted_server)
        users = connector.fetch_users()
        assert len(users) == 3
        assert all(isinstance(u, RawDeviceUser) for u in users)

        list_requests = [
            r["body"] for r in _ScriptedHandler.received_requests if r["body"]["cmd"] == "GetUserIdList"
        ]
        assert list_requests[0]["payload"] == {}
        assert list_requests[1]["payload"] == {"start_pos": 50}

    def test_user_not_found_between_list_and_info_is_skipped_not_raised(self, scripted_server):
        _ScriptedHandler.responses = {
            "GetUserIdList": {"mid": "1", "result": "UserIdList", "payload": {"user_id": ["1", "2"]}},
            "GetUserInfo": [
                {"mid": "1", "result": "Error", "payload": {"code": "user_id_not_exists"}},
                {"mid": "2", "result": "UserInfo", "payload": {"id": "2", "name": "Bob"}},
            ],
        }
        connector = _connector(scripted_server)
        users = connector.fetch_users()
        assert len(users) == 1
        assert users[0].device_user_reference == "2"


class TestAttendLogPagination:
    def test_follows_next_pos_and_maps_to_check_in(self, scripted_server):
        _ScriptedHandler.responses = {
            "GetAttendLog": [
                {
                    "mid": "1",
                    "result": "AttendLog",
                    "payload": {
                        "logs": [{"user_id": "1", "time": "2020-04-01T08:10:05", "mode": "face"}],
                        "next_pos": 50,
                    },
                },
                {
                    "mid": "2",
                    "result": "AttendLog",
                    "payload": {
                        "logs": [{"user_id": "2", "time": "2020-04-01T17:00:00", "mode": "fp"}]
                    },
                },
            ]
        }
        connector = _connector(scripted_server)
        logs = connector.fetch_attendance_logs()
        assert len(logs) == 2
        assert all(log.punch_type == PunchType.CHECK_IN for log in logs)
        assert logs[0].device_user_reference == "1"
        assert logs[0].punch_time.isoformat() == "2020-04-01T08:10:05+00:00"

        log_requests = [
            r["body"] for r in _ScriptedHandler.received_requests if r["body"]["cmd"] == "GetAttendLog"
        ]
        assert log_requests[0]["payload"] == {}
        assert log_requests[1]["payload"] == {"start_pos": 50}

    def test_never_sends_erase_attend_log(self, scripted_server):
        connector = _connector(scripted_server)
        connector.fetch_attendance_logs()
        commands_sent = {r["body"]["cmd"] for r in _ScriptedHandler.received_requests}
        assert "EraseAttendLog" not in commands_sent


class TestPushUsers:
    def test_sends_set_user_info_with_only_id_and_name(self, scripted_server):
        connector = _connector(scripted_server)
        connector.push_users([RawDeviceUser(device_user_reference="42", name="Jane Doe")])
        request = _ScriptedHandler.received_requests[-1]
        assert request["body"]["cmd"] == "SetUserInfo"
        assert request["body"]["payload"] == {"id": "42", "name": "Jane Doe"}


class TestGetUserBiometricStatus:
    def test_reports_fingerprint_count_and_card(self, scripted_server):
        _ScriptedHandler.responses = {
            "GetUserInfo": {
                "mid": "1",
                "result": "UserInfo",
                "payload": {"id": "1", "fp": ["a", "b"], "card": "12345"},
            }
        }
        connector = _connector(scripted_server)
        status = connector.get_user_biometric_status("1")
        assert status.fingerprint_count == 2
        assert status.card_assigned is True
        assert status.face_registered is False
        assert status.palm_registered is False

    def test_reports_face_and_palm_registration(self, scripted_server):
        _ScriptedHandler.responses = {
            "GetUserInfo": {
                "mid": "1",
                "result": "UserInfo",
                "payload": {"id": "1", "face": "base64-face-data", "palm": ["base64-palm-data"]},
            }
        }
        connector = _connector(scripted_server)
        status = connector.get_user_biometric_status("1")
        assert status.face_registered is True
        assert status.palm_registered is True

    def test_unknown_user_returns_zero_status_without_raising(self, scripted_server):
        _ScriptedHandler.responses = {
            "GetUserInfo": {
                "mid": "1",
                "result": "Error",
                "payload": {"code": "user_id_not_exists"},
            }
        }
        connector = _connector(scripted_server)
        status = connector.get_user_biometric_status("999")
        assert status.fingerprint_count == 0
        assert status.card_assigned is False
        assert status.face_registered is False
        assert status.palm_registered is False


class TestNeverSendsDestructiveCommands:
    def test_no_public_method_issues_erase_or_clear_commands(self, scripted_server):
        _ScriptedHandler.responses = {
            "GetUserIdList": {"mid": "1", "result": "UserIdList", "payload": {"user_id": []}},
            "BeginEnrollFace": {"mid": "1", "result": "JobCreated", "payload": {"job_id": 1}},
        }
        connector = _connector(scripted_server)
        connector.test_connection()
        connector.fetch_users()
        connector.fetch_attendance_logs()
        connector.begin_face_enrollment()
        connector.cancel_all_enrollment_jobs()
        connector.disconnect()

        commands_sent = {r["body"]["cmd"] for r in _ScriptedHandler.received_requests}
        destructive = {
            "EraseAttendLog",
            "ClearUsers",
            "ClearAllData",
            "SetSecurityConfig",
            "BeginEnrollCard",
            "PhotoToFacedata",
        }
        assert commands_sent.isdisjoint(destructive)


class TestBiometricEnrollment:
    """On-device face/fingerprint/palm capture, per the official document's
    "Biometric Enrollment" section (BeginEnrollFace/BeginEnrollFp/
    BeginEnrollPalm, QueryJobStatus, CancelJob, CancelAllJobs) -- and
    attaching a captured template to an employee via SetUserInfo.
    """

    def test_begin_face_enrollment_sends_documented_command_and_returns_pending_job(
        self, scripted_server
    ):
        _ScriptedHandler.responses = {
            "BeginEnrollFace": {"mid": "1", "result": "JobCreated", "payload": {"job_id": 7}},
        }
        connector = _connector(scripted_server)
        job = connector.begin_face_enrollment()
        assert _ScriptedHandler.received_requests[-1]["body"]["cmd"] == "BeginEnrollFace"
        assert _ScriptedHandler.received_requests[-1]["body"]["payload"] == {}
        assert job == DeliEnrollmentJob(job_id=7, kind=ENROLLMENT_KIND_FACE, state=JOB_STATE_PENDING)

    def test_begin_fingerprint_enrollment_sends_begin_enroll_fp(self, scripted_server):
        _ScriptedHandler.responses = {
            "BeginEnrollFp": {"mid": "1", "result": "JobCreated", "payload": {"job_id": 3}},
        }
        connector = _connector(scripted_server)
        job = connector.begin_fingerprint_enrollment()
        assert _ScriptedHandler.received_requests[-1]["body"]["cmd"] == "BeginEnrollFp"
        assert job.kind == ENROLLMENT_KIND_FINGERPRINT

    def test_begin_palm_enrollment_sends_begin_enroll_palm(self, scripted_server):
        _ScriptedHandler.responses = {
            "BeginEnrollPalm": {"mid": "1", "result": "JobCreated", "payload": {"job_id": 9}},
        }
        connector = _connector(scripted_server)
        job = connector.begin_palm_enrollment()
        assert _ScriptedHandler.received_requests[-1]["body"]["cmd"] == "BeginEnrollPalm"
        assert job.kind == ENROLLMENT_KIND_PALM

    def test_query_job_status_pending_has_no_template_data(self, scripted_server):
        _ScriptedHandler.responses = {
            "QueryJobStatus": {
                "mid": "1",
                "result": "JobStatus",
                "payload": {"job_id": 7, "state": "pending"},
            },
        }
        connector = _connector(scripted_server)
        job = DeliEnrollmentJob(job_id=7, kind=ENROLLMENT_KIND_FACE, state=JOB_STATE_PENDING)
        updated = connector.query_enrollment_job(job)
        assert _ScriptedHandler.received_requests[-1]["body"]["payload"] == {"job_id": 7}
        assert updated.state == JOB_STATE_PENDING
        assert updated.template_data is None

    def test_query_job_status_succeeded_face_returns_face_data(self, scripted_server):
        _ScriptedHandler.responses = {
            "QueryJobStatus": {
                "mid": "1",
                "result": "JobStatus",
                "payload": {"job_id": 7, "state": "succeeded", "face_data": "b64face"},
            },
        }
        connector = _connector(scripted_server)
        job = DeliEnrollmentJob(job_id=7, kind=ENROLLMENT_KIND_FACE, state=JOB_STATE_PENDING)
        updated = connector.query_enrollment_job(job)
        assert updated.state == JOB_STATE_SUCCEEDED
        assert updated.template_data == "b64face"

    def test_query_job_status_succeeded_fingerprint_returns_fp_data(self, scripted_server):
        _ScriptedHandler.responses = {
            "QueryJobStatus": {
                "mid": "1",
                "result": "JobStatus",
                "payload": {"job_id": 3, "state": "succeeded", "fp_data": "b64fp"},
            },
        }
        connector = _connector(scripted_server)
        job = DeliEnrollmentJob(job_id=3, kind=ENROLLMENT_KIND_FINGERPRINT, state=JOB_STATE_PENDING)
        updated = connector.query_enrollment_job(job)
        assert updated.state == JOB_STATE_SUCCEEDED
        assert updated.template_data == "b64fp"

    def test_query_job_status_failed_has_no_template_data(self, scripted_server):
        _ScriptedHandler.responses = {
            "QueryJobStatus": {
                "mid": "1",
                "result": "JobStatus",
                "payload": {"job_id": 7, "state": "failed"},
            },
        }
        connector = _connector(scripted_server)
        job = DeliEnrollmentJob(job_id=7, kind=ENROLLMENT_KIND_FACE, state=JOB_STATE_PENDING)
        updated = connector.query_enrollment_job(job)
        assert updated.state == JOB_STATE_FAILED
        assert updated.template_data is None

    def test_cancel_enrollment_job_sends_cancel_job_with_id(self, scripted_server):
        connector = _connector(scripted_server)
        job = DeliEnrollmentJob(job_id=42, kind=ENROLLMENT_KIND_FACE, state=JOB_STATE_PENDING)
        connector.cancel_enrollment_job(job)
        request = _ScriptedHandler.received_requests[-1]
        assert request["body"]["cmd"] == "CancelJob"
        assert request["body"]["payload"] == {"job_id": 42}

    def test_cancel_all_enrollment_jobs_sends_cancel_all_jobs(self, scripted_server):
        connector = _connector(scripted_server)
        connector.cancel_all_enrollment_jobs()
        request = _ScriptedHandler.received_requests[-1]
        assert request["body"]["cmd"] == "CancelAllJobs"
        assert request["body"]["payload"] == {}

    def test_set_user_face_template_sends_face_as_single_string(self, scripted_server):
        connector = _connector(scripted_server)
        connector.set_user_face_template("42", "b64face")
        request = _ScriptedHandler.received_requests[-1]
        assert request["body"]["cmd"] == "SetUserInfo"
        assert request["body"]["payload"] == {"id": "42", "face": "b64face"}

    def test_set_user_fingerprint_template_sends_fp_as_array(self, scripted_server):
        connector = _connector(scripted_server)
        connector.set_user_fingerprint_template("42", "b64fp")
        request = _ScriptedHandler.received_requests[-1]
        assert request["body"]["payload"] == {"id": "42", "fp": ["b64fp"]}

    def test_set_user_palm_template_sends_palm_as_array(self, scripted_server):
        connector = _connector(scripted_server)
        connector.set_user_palm_template("42", "b64palm")
        request = _ScriptedHandler.received_requests[-1]
        assert request["body"]["payload"] == {"id": "42", "palm": ["b64palm"]}


class TestDeviceManagerWiring:
    def test_deli_es172_protocol_resolves_to_the_real_connector(self):
        from devices.device_manager import DeviceManager
        from models.device import Device
        from models.enums import DeviceProtocol

        device = Device(
            name="DELI test",
            protocol=DeviceProtocol.DELI_ES172,
            host="192.0.2.1",
            port=80,
            communication_key=None,
        )
        connector = DeviceManager().get_connector(device)
        assert isinstance(connector, DeliES172Connector)

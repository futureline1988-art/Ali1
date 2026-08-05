"""Tests for DeviceService's biometric-status refresh and face-enrollment workflow.

Uses a real SQLite database (see ``tests/conftest.py``'s own
philosophy) plus the fake ``zk`` package from
``tests/test_device_zkteco.py`` to simulate a real ZKTeco device
without hardware -- exercising the actual :class:`~devices.device_manager.DeviceManager`
-> :class:`~devices.zkteco_device.ZKTecoConnector` path end to end,
not a mocked service.
"""

from __future__ import annotations

import pytest

from database.database import session_scope
from models.enums import DeviceProtocol
from services.device_service import DeviceService, FaceEnrollmentOutcome
from services.employee_service import EmployeeService
from tests.test_device_zkteco import fake_zkteco_device  # noqa: F401 - reused fixture


@pytest.fixture
def zkteco_device_id(company_factory, fake_zkteco_device):
    company_id = company_factory()
    with session_scope() as session:
        device = DeviceService(session, company_id=company_id).create_device(
            name="جهاز الاختبار",
            protocol=DeviceProtocol.ZKTECO_TCP,
            host="10.0.0.99",
            port=4370,
        )
        return company_id, device.id


@pytest.fixture
def employee_id(zkteco_device_id):
    company_id, _device_id = zkteco_device_id
    with session_scope() as session:
        employee = EmployeeService(session, company_id=company_id).create_employee(
            employee_number="9001", full_name="محمد أحمد"
        )
        return employee.id


class TestBeginFaceEnrollment:
    def test_unsupported_device_when_face_function_off(self, zkteco_device_id, employee_id):
        company_id, device_id = zkteco_device_id
        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            employee = service.employee_repo.get_by_id(employee_id)
            result = service.begin_face_enrollment(device, employee)

        assert result.outcome is FaceEnrollmentOutcome.UNSUPPORTED_DEVICE

    def test_invalid_employee_number_is_rejected_before_touching_the_device(
        self, zkteco_device_id, fake_zkteco_device, company_factory
    ):
        company_id, device_id = zkteco_device_id
        fake_zkteco_device.enable_face()
        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            employee = EmployeeService(session, company_id=company_id).create_employee(
                employee_number="EMP-0042", full_name="Non Numeric"
            )
            device = service.device_repo.get_by_id(device_id)
            result = service.begin_face_enrollment(device, employee)

        assert result.outcome is FaceEnrollmentOutcome.INVALID_EMPLOYEE_NUMBER
        assert len(fake_zkteco_device.users) == 0  # never pushed

    def test_starts_successfully_on_a_face_capable_device(
        self, zkteco_device_id, employee_id, fake_zkteco_device
    ):
        company_id, device_id = zkteco_device_id
        fake_zkteco_device.enable_face(capacity=50)
        fake_zkteco_device.faces = 2

        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            employee = service.employee_repo.get_by_id(employee_id)
            result = service.begin_face_enrollment(device, employee)

        assert result.outcome is FaceEnrollmentOutcome.STARTED
        assert result.session is not None
        assert result.session.before_face_count == 2
        assert result.session.device_id == device_id
        assert result.session.employee_id == employee_id
        # The employee was actually pushed to the (fake) device.
        assert any(u.user_id == "9001" for u in fake_zkteco_device.users.values())


class TestConfirmFaceEnrollment:
    def test_not_detected_when_face_count_has_not_changed(
        self, zkteco_device_id, employee_id, fake_zkteco_device
    ):
        company_id, device_id = zkteco_device_id
        fake_zkteco_device.enable_face()
        fake_zkteco_device.faces = 5

        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            employee = service.employee_repo.get_by_id(employee_id)
            start_result = service.begin_face_enrollment(device, employee)

            # No new enrollment happens on the device in between.
            confirm_result = service.confirm_face_enrollment(
                device, employee, start_result.session, operator_confirmed=True
            )

        assert confirm_result.outcome is FaceEnrollmentOutcome.NOT_DETECTED

    def test_detected_but_requires_operator_confirmation_before_recording(
        self, zkteco_device_id, employee_id, fake_zkteco_device
    ):
        company_id, device_id = zkteco_device_id
        fake_zkteco_device.enable_face()
        fake_zkteco_device.faces = 1

        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            employee = service.employee_repo.get_by_id(employee_id)
            start_result = service.begin_face_enrollment(device, employee)

            fake_zkteco_device.faces = 2  # simulates the physical enrollment happening

            unconfirmed_result = service.confirm_face_enrollment(
                device, employee, start_result.session, operator_confirmed=False
            )
            assert unconfirmed_result.outcome is FaceEnrollmentOutcome.NOT_DETECTED

            refreshed_employee = service.employee_repo.get_by_id(employee_id)
            assert refreshed_employee.face_enrolled is False

    def test_confirmed_updates_employee_biometric_status(
        self, zkteco_device_id, employee_id, fake_zkteco_device
    ):
        company_id, device_id = zkteco_device_id
        fake_zkteco_device.enable_face()
        fake_zkteco_device.faces = 0

        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            employee = service.employee_repo.get_by_id(employee_id)
            start_result = service.begin_face_enrollment(device, employee)

            fake_zkteco_device.faces = 1  # physical enrollment happened

            confirm_result = service.confirm_face_enrollment(
                device, employee, start_result.session, operator_confirmed=True
            )

            assert confirm_result.outcome is FaceEnrollmentOutcome.CONFIRMED
            refreshed_employee = service.employee_repo.get_by_id(employee_id)
            assert refreshed_employee.face_enrolled is True
            assert refreshed_employee.face_enrolled_device_id == device_id
            assert refreshed_employee.face_enrolled_at is not None
            assert refreshed_employee.biometric_last_verification_result == "confirmed"


class TestResetFaceEnrollmentStatus:
    def test_clears_local_status_without_touching_the_device(
        self, zkteco_device_id, employee_id, fake_zkteco_device
    ):
        company_id, device_id = zkteco_device_id
        fake_zkteco_device.enable_face()
        fake_zkteco_device.faces = 0

        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            employee = service.employee_repo.get_by_id(employee_id)
            start_result = service.begin_face_enrollment(device, employee)
            fake_zkteco_device.faces = 1
            service.confirm_face_enrollment(
                device, employee, start_result.session, operator_confirmed=True
            )

        faces_before_reset = fake_zkteco_device.faces
        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            employee = service.employee_repo.get_by_id(employee_id)
            service.reset_face_enrollment_status(employee)

        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            refreshed = service.employee_repo.get_by_id(employee_id)
            assert refreshed.face_enrolled is False
            assert refreshed.face_enrolled_device_id is None
            assert refreshed.biometric_last_verification_result == "local_reset"
        # The device's own state is untouched by a local-only reset.
        assert fake_zkteco_device.faces == faces_before_reset


class TestRefreshEmployeeBiometricStatus:
    def test_refreshes_fingerprint_count_and_card_from_the_device(
        self, zkteco_device_id, employee_id, fake_zkteco_device
    ):
        from tests.test_device_zkteco import _FakeFinger, _FakeUser

        company_id, device_id = zkteco_device_id
        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            employee = service.employee_repo.get_by_id(employee_id)
            service.push_employee_to_device(device, employee)

        pushed_uid = next(iter(fake_zkteco_device.users.values())).uid
        fake_zkteco_device.users[pushed_uid] = _FakeUser(
            pushed_uid, "9001", "محمد أحمد", card=777
        )
        fake_zkteco_device.fingers = [
            _FakeFinger(uid=pushed_uid, fid=0, valid=1, template=b"x"),
            _FakeFinger(uid=pushed_uid, fid=1, valid=1, template=b"y"),
        ]

        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            employee = service.employee_repo.get_by_id(employee_id)
            service.refresh_employee_biometric_status(device, employee)

        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            refreshed = service.employee_repo.get_by_id(employee_id)
            assert refreshed.fingerprint_count == 2
            assert refreshed.card_assigned is True
            assert refreshed.biometric_last_synced_at is not None

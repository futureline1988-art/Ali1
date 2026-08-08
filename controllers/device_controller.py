"""Device controller: bridges the devices screen to ``DeviceService``."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from sqlalchemy.orm import Session

from datetime import datetime

from controllers.base_controller import BaseController, requires_permission
from devices.device_interface import ConnectionTestResult, DeviceCapabilities
from devices.device_manager import DeviceManager
from models.device import Device
from models.employee import Employee
from models.enums import DeviceProtocol
from repositories.device_repository import DeviceRepository
from repositories.employee_repository import EmployeeRepository
from devices.deli_es172_device import DeliEnrollmentJob
from services.device_service import (
    DeliEnrollmentResult,
    DeviceService,
    FaceEnrollmentResult,
    FaceEnrollmentSession,
)


def _device_to_dict(device: Device) -> dict[str, Any]:
    """Serialize a device plus its display labels, while the session is open."""
    data = device.to_dict(exclude={"communication_key"})
    data["protocol_label_ar"] = device.protocol_label_ar
    data["protocol_label_en"] = device.protocol_label_en
    data["status_label_ar"] = device.status_label_ar
    data["status_label_en"] = device.status_label_en
    return data


def _capabilities_to_dict(capabilities: DeviceCapabilities) -> dict[str, Any]:
    """Serialize a :class:`~devices.device_interface.DeviceCapabilities` result."""
    return {
        "supports_face": capabilities.supports_face,
        "face_template_count": capabilities.face_template_count,
        "face_capacity": capabilities.face_capacity,
        "supports_fingerprint": capabilities.supports_fingerprint,
        "fingerprint_template_count": capabilities.fingerprint_template_count,
        "fingerprint_capacity": capabilities.fingerprint_capacity,
        "user_count": capabilities.user_count,
        "user_capacity": capabilities.user_capacity,
        "attendance_log_count": capabilities.attendance_log_count,
        "serial_number": capabilities.serial_number,
        "device_model": capabilities.device_model,
        "firmware_version": capabilities.firmware_version,
    }


def _connection_test_result_to_dict(result: ConnectionTestResult) -> dict[str, Any]:
    """Serialize a :class:`~devices.device_interface.ConnectionTestResult` for the UI."""
    return {
        "success": result.success,
        "message_ar": result.message_ar,
        "detail": result.detail,
        "capabilities": _capabilities_to_dict(result.capabilities)
        if result.capabilities is not None
        else None,
    }


def _face_enrollment_session_to_dict(session: FaceEnrollmentSession) -> dict[str, Any]:
    """Serialize a :class:`~services.device_service.FaceEnrollmentSession` for the UI to hold onto."""
    return {
        "device_id": session.device_id,
        "employee_id": session.employee_id,
        "before_face_count": session.before_face_count,
        "started_at": session.started_at.isoformat(),
    }


def _face_enrollment_session_from_dict(data: dict[str, Any]) -> FaceEnrollmentSession:
    """Reconstruct a :class:`~services.device_service.FaceEnrollmentSession` the UI passed back in."""
    return FaceEnrollmentSession(
        device_id=data["device_id"],
        employee_id=data["employee_id"],
        before_face_count=data["before_face_count"],
        started_at=datetime.fromisoformat(data["started_at"]),
    )


def _face_enrollment_result_to_dict(result: FaceEnrollmentResult) -> dict[str, Any]:
    """Serialize a :class:`~services.device_service.FaceEnrollmentResult`."""
    return {
        "outcome": result.outcome.value,
        "message_ar": result.message_ar,
        "message_en": result.message_en,
        "session": (
            _face_enrollment_session_to_dict(result.session) if result.session is not None else None
        ),
    }


def _deli_enrollment_job_to_dict(job: DeliEnrollmentJob) -> dict[str, Any]:
    """Serialize a :class:`~devices.deli_es172_device.DeliEnrollmentJob`.

    ``template_data`` is intentionally included -- the UI never
    persists it (only :meth:`~controllers.device_controller.DeviceController.confirm_deli_enrollment`
    does, via ``SetUserInfo``), but it must round-trip through the
    dict the UI holds between a successful poll and the confirm click.
    """
    return {
        "job_id": job.job_id,
        "kind": job.kind,
        "state": job.state,
        "template_data": job.template_data,
    }


def _deli_enrollment_job_from_dict(data: dict[str, Any]) -> DeliEnrollmentJob:
    """Reconstruct a :class:`~devices.deli_es172_device.DeliEnrollmentJob` the UI passed back in."""
    return DeliEnrollmentJob(
        job_id=data["job_id"],
        kind=data["kind"],
        state=data["state"],
        template_data=data.get("template_data"),
    )


def _deli_enrollment_result_to_dict(result: DeliEnrollmentResult) -> dict[str, Any]:
    """Serialize a :class:`~services.device_service.DeliEnrollmentResult`."""
    return {
        "outcome": result.outcome.value,
        "message_ar": result.message_ar,
        "message_en": result.message_en,
        "job": _deli_enrollment_job_to_dict(result.job) if result.job is not None else None,
    }


def _employee_biometric_status_to_dict(employee: Employee) -> dict[str, Any]:
    """Serialize just the biometric-status fields of an employee, while the session is open."""
    return {
        "id": employee.id,
        "employee_number": employee.employee_number,
        "full_name": employee.full_name,
        "face_enrolled": employee.face_enrolled,
        "face_enrolled_device_id": employee.face_enrolled_device_id,
        "face_enrolled_at": (
            employee.face_enrolled_at.isoformat() if employee.face_enrolled_at else None
        ),
        "fingerprint_count": employee.fingerprint_count,
        "card_assigned": employee.card_assigned,
        "palm_registered": employee.palm_registered,
        "biometric_last_synced_at": (
            employee.biometric_last_synced_at.isoformat()
            if employee.biometric_last_synced_at
            else None
        ),
        "biometric_last_verification_result": employee.biometric_last_verification_result,
    }


class DeviceController(BaseController):
    """Controller for the biometric devices management screen."""

    devices_changed = Signal()
    """Emitted after any successful create/update/delete/sync."""

    def __init__(
        self,
        *,
        company_id: int,
        actor_user_id: int | None = None,
        permission_codes: frozenset[str] = frozenset(),
        device_manager: DeviceManager | None = None,
    ) -> None:
        """Create a device controller.

        Args:
            company_id: The company to scope every operation to.
            actor_user_id: The current user, for audit attribution.
            permission_codes: The current user's granted permission
                codes.
            device_manager: Custom device manager, primarily for
                injecting a fake connector in tests.
        """
        super().__init__(
            company_id=company_id,
            actor_user_id=actor_user_id,
            permission_codes=permission_codes,
        )
        self._device_manager = device_manager

    @requires_permission("devices.manage")
    def create_device(
        self,
        *,
        name: str,
        protocol: DeviceProtocol,
        host: str,
        port: int,
        branch_id: int | None = None,
        communication_key: str | None = None,
        timeout_seconds: int | None = None,
        notes: str | None = None,
    ) -> dict[str, Any] | None:
        """Register a new device.

        Args mirror :meth:`~services.device_service.DeviceService.create_device`.

        Returns:
            The new device's data as a dict, or ``None`` on failure.
        """

        def do_create(session: Session) -> dict[str, Any]:
            service = DeviceService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            device = service.create_device(
                name=name,
                protocol=protocol,
                host=host,
                port=port,
                branch_id=branch_id,
                communication_key=communication_key,
                timeout_seconds=timeout_seconds,
                notes=notes,
            )
            return _device_to_dict(device)

        result = self._run(do_create)
        if result is not None:
            self.devices_changed.emit()
        return result

    @requires_permission("devices.manage")
    def update_device(self, device_id: int, **fields: Any) -> dict[str, Any] | None:
        """Update an existing device.

        Args:
            device_id: The device to update.
            **fields: See
                :meth:`~services.device_service.DeviceService.update_device`.

        Returns:
            The updated device's data as a dict, or ``None`` on
            failure.
        """

        def do_update(session: Session) -> dict[str, Any]:
            repo = DeviceRepository(session, company_id=self.company_id)
            device = repo.get_by_id(device_id)
            if device is None:
                raise ValueError(f"Device {device_id!r} was not found.")
            service = DeviceService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            updated = service.update_device(device, **fields)
            return _device_to_dict(updated)

        result = self._run(do_update)
        if result is not None:
            self.devices_changed.emit()
        return result

    @requires_permission("devices.manage", default=False)
    def delete_device(self, device_id: int) -> bool:
        """Soft-delete a device.

        Args:
            device_id: The device to delete.

        Returns:
            ``True`` on success, ``False`` on failure.
        """

        def do_delete(session: Session) -> bool:
            repo = DeviceRepository(session, company_id=self.company_id)
            device = repo.get_by_id(device_id)
            if device is None:
                raise ValueError(f"Device {device_id!r} was not found.")
            service = DeviceService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            service.delete_device(device)
            return True

        result = self._run(do_delete)
        if result:
            self.devices_changed.emit()
        return bool(result)

    @requires_permission("devices.view", "devices.manage", default=False)
    def test_connection(self, device_id: int) -> dict[str, Any] | None:
        """Test connectivity to a device, update its recorded status, and diagnose any failure.

        Args:
            device_id: The device to test.

        Returns:
            The diagnostic result as a dict (``success``, ``message_ar``,
            ``detail``, ``capabilities``), or ``None`` on an
            unexpected controller-level failure (e.g. unknown device).
        """

        def do_test(session: Session) -> dict[str, Any]:
            repo = DeviceRepository(session, company_id=self.company_id)
            device = repo.get_by_id(device_id)
            if device is None:
                raise ValueError(f"Device {device_id!r} was not found.")
            service = DeviceService(
                session,
                company_id=self.company_id,
                actor_user_id=self.actor_user_id,
                device_manager=self._device_manager,
            )
            return _connection_test_result_to_dict(service.test_connection(device))

        result = self._run(do_test)
        self.devices_changed.emit()
        return result

    @requires_permission("devices.manage", default=0)
    def sync_attendance_logs(self, device_id: int) -> int:
        """Download and persist a device's attendance logs.

        Args:
            device_id: The device to sync.

        Returns:
            The number of new punches created, or ``0`` on failure.
        """

        def do_sync(session: Session) -> int:
            repo = DeviceRepository(session, company_id=self.company_id)
            device = repo.get_by_id(device_id)
            if device is None:
                raise ValueError(f"Device {device_id!r} was not found.")
            service = DeviceService(
                session,
                company_id=self.company_id,
                actor_user_id=self.actor_user_id,
                device_manager=self._device_manager,
            )
            return len(service.sync_attendance_logs(device))

        result = self._run(do_sync)
        if result:
            self.devices_changed.emit()
        return result or 0

    @requires_permission("devices.manage", default=False)
    def push_employee_to_device(self, *, device_id: int, employee_id: int) -> bool:
        """Enroll one employee on a device.

        Args:
            device_id: The device to push to.
            employee_id: The employee to enroll.

        Returns:
            ``True`` on success, ``False`` on failure.
        """

        def do_push(session: Session) -> bool:
            device_repo = DeviceRepository(session, company_id=self.company_id)
            device = device_repo.get_by_id(device_id)
            if device is None:
                raise ValueError(f"Device {device_id!r} was not found.")
            employee_repo = EmployeeRepository(session, company_id=self.company_id)
            employee = employee_repo.get_by_id(employee_id)
            if employee is None:
                raise ValueError(f"Employee {employee_id!r} was not found.")
            service = DeviceService(
                session,
                company_id=self.company_id,
                actor_user_id=self.actor_user_id,
                device_manager=self._device_manager,
            )
            service.push_employee_to_device(device, employee)
            return True

        return bool(self._run(do_push))

    @requires_permission("devices.manage", default=0)
    def pull_employees_from_device(self, device_id: int) -> int:
        """Download every user enrolled on a device and import unmatched ones as employees.

        Args:
            device_id: The device to pull from.

        Returns:
            How many new employees were created, or ``0`` on failure.
        """

        def do_pull(session: Session) -> int:
            device_repo = DeviceRepository(session, company_id=self.company_id)
            device = device_repo.get_by_id(device_id)
            if device is None:
                raise ValueError(f"Device {device_id!r} was not found.")
            service = DeviceService(
                session,
                company_id=self.company_id,
                actor_user_id=self.actor_user_id,
                device_manager=self._device_manager,
            )
            return len(service.pull_employees_from_device(device))

        result = self._run(do_pull)
        if result:
            self.devices_changed.emit()
        return result or 0

    @requires_permission("devices.view", "devices.manage", default=None)
    def get_device_capabilities(self, device_id: int) -> dict[str, Any] | None:
        """Read a device's identity, capacity, and biometric support.

        Args:
            device_id: The device to query.

        Returns:
            The device's capabilities as a dict, or ``None`` on
            failure (e.g. the device could not be reached).
        """

        def do_get(session: Session) -> dict[str, Any]:
            repo = DeviceRepository(session, company_id=self.company_id)
            device = repo.get_by_id(device_id)
            if device is None:
                raise ValueError(f"Device {device_id!r} was not found.")
            service = DeviceService(
                session,
                company_id=self.company_id,
                actor_user_id=self.actor_user_id,
                device_manager=self._device_manager,
            )
            return _capabilities_to_dict(service.get_device_capabilities(device))

        result = self._run(do_get)
        self.devices_changed.emit()
        return result

    @requires_permission("devices.manage", default=None)
    def begin_face_enrollment(self, *, device_id: int, employee_id: int) -> dict[str, Any] | None:
        """Start the on-device face-enrollment workflow for one employee.

        Args:
            device_id: The device to enroll on.
            employee_id: The employee to enroll.

        Returns:
            A :class:`~services.device_service.FaceEnrollmentResult`
            as a dict (see
            :meth:`~services.device_service.DeviceService.begin_face_enrollment`'s
            own docstring for what ``outcome`` can be), or ``None`` on
            an unexpected failure.
        """

        def do_begin(session: Session) -> dict[str, Any]:
            device_repo = DeviceRepository(session, company_id=self.company_id)
            device = device_repo.get_by_id(device_id)
            if device is None:
                raise ValueError(f"Device {device_id!r} was not found.")
            employee_repo = EmployeeRepository(session, company_id=self.company_id)
            employee = employee_repo.get_by_id(employee_id)
            if employee is None:
                raise ValueError(f"Employee {employee_id!r} was not found.")
            service = DeviceService(
                session,
                company_id=self.company_id,
                actor_user_id=self.actor_user_id,
                device_manager=self._device_manager,
            )
            return _face_enrollment_result_to_dict(service.begin_face_enrollment(device, employee))

        return self._run(do_begin)

    @requires_permission("devices.view", "devices.manage", default=None)
    def check_face_enrollment_progress(self, device_id: int) -> int | None:
        """Read the device's current enrolled-face count, for a non-blocking progress poll.

        Args:
            device_id: The device to poll.

        Returns:
            The device-wide enrolled-face count, or ``None`` if it
            could not be determined (including on failure -- a poll
            tick failing must not be treated as "enrollment failed" by
            the caller, only as "no update this tick").
        """

        def do_check(session: Session) -> int | None:
            repo = DeviceRepository(session, company_id=self.company_id)
            device = repo.get_by_id(device_id)
            if device is None:
                raise ValueError(f"Device {device_id!r} was not found.")
            service = DeviceService(
                session,
                company_id=self.company_id,
                actor_user_id=self.actor_user_id,
                device_manager=self._device_manager,
            )
            return service.check_face_enrollment_progress(device)

        return self._run(do_check)

    @requires_permission("devices.manage", default=None)
    def confirm_face_enrollment(
        self,
        *,
        device_id: int,
        employee_id: int,
        enrollment_session: dict[str, Any],
        operator_confirmed: bool,
    ) -> dict[str, Any] | None:
        """Verify and record the outcome of a face-enrollment attempt.

        Args:
            device_id: The device enrollment was attempted on.
            employee_id: The employee enrollment was attempted for.
            enrollment_session: The ``session`` dict returned by
                :meth:`begin_face_enrollment`.
            operator_confirmed: Whether the operator has confirmed a
                newly-detected enrollment belongs to this employee.

        Returns:
            A :class:`~services.device_service.FaceEnrollmentResult`
            as a dict, or ``None`` on an unexpected failure.
        """

        def do_confirm(session: Session) -> dict[str, Any]:
            device_repo = DeviceRepository(session, company_id=self.company_id)
            device = device_repo.get_by_id(device_id)
            if device is None:
                raise ValueError(f"Device {device_id!r} was not found.")
            employee_repo = EmployeeRepository(session, company_id=self.company_id)
            employee = employee_repo.get_by_id(employee_id)
            if employee is None:
                raise ValueError(f"Employee {employee_id!r} was not found.")
            service = DeviceService(
                session,
                company_id=self.company_id,
                actor_user_id=self.actor_user_id,
                device_manager=self._device_manager,
            )
            result = service.confirm_face_enrollment(
                device,
                employee,
                _face_enrollment_session_from_dict(enrollment_session),
                operator_confirmed=operator_confirmed,
            )
            return _face_enrollment_result_to_dict(result)

        result = self._run(do_confirm)
        if result is not None:
            self.devices_changed.emit()
        return result

    @requires_permission("devices.manage", default=False)
    def cancel_face_enrollment(self, employee_id: int) -> bool:
        """Record that an in-progress face-enrollment attempt was cancelled.

        Args:
            employee_id: The employee whose enrollment attempt was
                cancelled.

        Returns:
            ``True`` on success, ``False`` on failure.
        """

        def do_cancel(session: Session) -> bool:
            employee_repo = EmployeeRepository(session, company_id=self.company_id)
            employee = employee_repo.get_by_id(employee_id)
            if employee is None:
                raise ValueError(f"Employee {employee_id!r} was not found.")
            service = DeviceService(
                session,
                company_id=self.company_id,
                actor_user_id=self.actor_user_id,
                device_manager=self._device_manager,
            )
            service.cancel_face_enrollment(employee)
            return True

        return bool(self._run(do_cancel))

    @requires_permission("devices.manage", default=None)
    def reset_face_enrollment_status(self, employee_id: int) -> dict[str, Any] | None:
        """Locally clear an employee's cached face-enrollment status (device untouched).

        Args:
            employee_id: The employee to reset.

        Returns:
            The employee's updated biometric-status dict, or ``None``
            on failure.
        """

        def do_reset(session: Session) -> dict[str, Any]:
            employee_repo = EmployeeRepository(session, company_id=self.company_id)
            employee = employee_repo.get_by_id(employee_id)
            if employee is None:
                raise ValueError(f"Employee {employee_id!r} was not found.")
            service = DeviceService(
                session,
                company_id=self.company_id,
                actor_user_id=self.actor_user_id,
                device_manager=self._device_manager,
            )
            return _employee_biometric_status_to_dict(service.reset_face_enrollment_status(employee))

        result = self._run(do_reset)
        if result is not None:
            self.devices_changed.emit()
        return result

    # ------------------------------------------------------------------
    # DELI ES172 on-device biometric enrollment
    # ------------------------------------------------------------------

    @requires_permission("devices.manage", default=None)
    def begin_deli_enrollment(
        self, *, device_id: int, employee_id: int, kind: str
    ) -> dict[str, Any] | None:
        """Start on-device biometric capture for one employee on a DELI ES172.

        Args:
            device_id: The DELI ES172 device to enroll on.
            employee_id: The employee to enroll.
            kind: One of ``devices.deli_es172_device.ENROLLMENT_KIND_FACE``/
                ``ENROLLMENT_KIND_FINGERPRINT``/``ENROLLMENT_KIND_PALM``.

        Returns:
            A :class:`~services.device_service.DeliEnrollmentResult` as
            a dict, or ``None`` on an unexpected failure.
        """

        def do_begin(session: Session) -> dict[str, Any]:
            device_repo = DeviceRepository(session, company_id=self.company_id)
            device = device_repo.get_by_id(device_id)
            if device is None:
                raise ValueError(f"Device {device_id!r} was not found.")
            employee_repo = EmployeeRepository(session, company_id=self.company_id)
            employee = employee_repo.get_by_id(employee_id)
            if employee is None:
                raise ValueError(f"Employee {employee_id!r} was not found.")
            service = DeviceService(
                session,
                company_id=self.company_id,
                actor_user_id=self.actor_user_id,
                device_manager=self._device_manager,
            )
            return _deli_enrollment_result_to_dict(
                service.begin_deli_enrollment(device, employee, kind=kind)
            )

        return self._run(do_begin)

    @requires_permission("devices.view", "devices.manage", default=None)
    def poll_deli_enrollment_job(
        self, *, device_id: int, job: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Check one DELI enrollment job's current state.

        Args:
            device_id: The device the job is running on.
            job: The ``job`` dict from :meth:`begin_deli_enrollment` (or
                an earlier poll).

        Returns:
            A :class:`~services.device_service.DeliEnrollmentResult` as
            a dict, or ``None`` on an unexpected failure.
        """

        def do_poll(session: Session) -> dict[str, Any]:
            device_repo = DeviceRepository(session, company_id=self.company_id)
            device = device_repo.get_by_id(device_id)
            if device is None:
                raise ValueError(f"Device {device_id!r} was not found.")
            service = DeviceService(
                session,
                company_id=self.company_id,
                actor_user_id=self.actor_user_id,
                device_manager=self._device_manager,
            )
            result = service.poll_deli_enrollment_job(device, _deli_enrollment_job_from_dict(job))
            return _deli_enrollment_result_to_dict(result)

        return self._run(do_poll)

    @requires_permission("devices.manage", default=None)
    def confirm_deli_enrollment(
        self, *, device_id: int, employee_id: int, job: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Attach a succeeded DELI enrollment job's template to an employee.

        Args:
            device_id: The device the job ran on.
            employee_id: The employee to attach the template to.
            job: The ``job`` dict from a poll whose ``state`` is
                ``"succeeded"``.

        Returns:
            A :class:`~services.device_service.DeliEnrollmentResult` as
            a dict, or ``None`` on an unexpected failure.
        """

        def do_confirm(session: Session) -> dict[str, Any]:
            device_repo = DeviceRepository(session, company_id=self.company_id)
            device = device_repo.get_by_id(device_id)
            if device is None:
                raise ValueError(f"Device {device_id!r} was not found.")
            employee_repo = EmployeeRepository(session, company_id=self.company_id)
            employee = employee_repo.get_by_id(employee_id)
            if employee is None:
                raise ValueError(f"Employee {employee_id!r} was not found.")
            service = DeviceService(
                session,
                company_id=self.company_id,
                actor_user_id=self.actor_user_id,
                device_manager=self._device_manager,
            )
            result = service.confirm_deli_enrollment(
                device, employee, _deli_enrollment_job_from_dict(job)
            )
            return _deli_enrollment_result_to_dict(result)

        result = self._run(do_confirm)
        if result is not None:
            self.devices_changed.emit()
        return result

    @requires_permission("devices.manage", default=None)
    def cancel_deli_enrollment(self, *, device_id: int, job: dict[str, Any]) -> dict[str, Any] | None:
        """Cancel an in-progress DELI enrollment job.

        Args:
            device_id: The device the job is running on.
            job: The ``job`` dict to cancel.

        Returns:
            A :class:`~services.device_service.DeliEnrollmentResult` as
            a dict, or ``None`` on an unexpected failure.
        """

        def do_cancel(session: Session) -> dict[str, Any]:
            device_repo = DeviceRepository(session, company_id=self.company_id)
            device = device_repo.get_by_id(device_id)
            if device is None:
                raise ValueError(f"Device {device_id!r} was not found.")
            service = DeviceService(
                session,
                company_id=self.company_id,
                actor_user_id=self.actor_user_id,
                device_manager=self._device_manager,
            )
            result = service.cancel_deli_enrollment(device, _deli_enrollment_job_from_dict(job))
            return _deli_enrollment_result_to_dict(result)

        return self._run(do_cancel)

    @requires_permission("devices.view", "devices.manage", default=None)
    def refresh_employee_biometric_status(
        self, *, device_id: int, employee_id: int
    ) -> dict[str, Any] | None:
        """Refresh one employee's fingerprint-count/card-assignment from a device.

        Args:
            device_id: The device to read from.
            employee_id: The employee to refresh.

        Returns:
            The employee's updated biometric-status dict, or ``None``
            on failure.
        """

        def do_refresh(session: Session) -> dict[str, Any]:
            device_repo = DeviceRepository(session, company_id=self.company_id)
            device = device_repo.get_by_id(device_id)
            if device is None:
                raise ValueError(f"Device {device_id!r} was not found.")
            employee_repo = EmployeeRepository(session, company_id=self.company_id)
            employee = employee_repo.get_by_id(employee_id)
            if employee is None:
                raise ValueError(f"Employee {employee_id!r} was not found.")
            service = DeviceService(
                session,
                company_id=self.company_id,
                actor_user_id=self.actor_user_id,
                device_manager=self._device_manager,
            )
            return _employee_biometric_status_to_dict(
                service.refresh_employee_biometric_status(device, employee)
            )

        return self._run(do_refresh)

    @requires_permission("devices.view", "devices.manage", default=None)
    def get_employee_biometric_status(self, employee_id: int) -> dict[str, Any] | None:
        """Read an employee's last-known (cached) biometric status, without contacting a device.

        Args:
            employee_id: The employee to look up.

        Returns:
            The employee's biometric-status dict, or ``None`` on
            failure.
        """

        def do_get(session: Session) -> dict[str, Any]:
            employee_repo = EmployeeRepository(session, company_id=self.company_id)
            employee = employee_repo.get_by_id(employee_id)
            if employee is None:
                raise ValueError(f"Employee {employee_id!r} was not found.")
            return _employee_biometric_status_to_dict(employee)

        return self._run(do_get)

    @requires_permission("devices.view", "devices.manage", default=[])
    def list_devices(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        """List this company's devices.

        Args:
            active_only: Restrict to devices eligible for scheduled
                sync jobs.

        Returns:
            Matching devices' data as dicts; an empty list on failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            service = DeviceService(session, company_id=self.company_id)
            return [_device_to_dict(device) for device in service.list_devices(active_only=active_only)]

        return self._run(do_list) or []

"""Device management service: connection testing and attendance log sync.

Bridges the repository layer to whichever concrete protocol
implementation ``devices.device_manager.DeviceManager`` resolves for a
given :class:`~models.device.Device` (ZKTeco TCP/UDP, Hikvision — see
the ``devices/`` package). This service owns *what to do* with what a
device connector returns (map it to employees, persist punches,
dedup on re-sync, update device status, write the audit trail);
``devices/`` owns *how to talk to* the physical device.

Device-to-employee mapping convention: a device's raw user reference is
matched against :attr:`~models.employee.Employee.employee_number`. A
device log for a user reference with no matching employee is skipped,
not created as an orphaned punch.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from devices.device_interface import RawDeviceUser
from devices.device_manager import DeviceManager
from models.attendance import AttendancePunch
from models.audit_log import AuditLog
from models.device import Device
from models.employee import Employee
from models.enums import AttendanceSource, AuditAction, DeviceProtocol
from repositories.attendance_repository import AttendancePunchRepository
from repositories.audit_log_repository import AuditLogRepository
from repositories.branch_repository import BranchRepository
from repositories.device_repository import DeviceRepository
from repositories.employee_repository import EmployeeRepository
from utils.logger import logger
from utils.validators import is_valid_host, is_valid_port, is_within_length

_UPDATABLE_FIELDS = frozenset(
    {"name", "host", "port", "branch_id", "communication_key", "is_active", "timeout_seconds", "notes"}
)


class DeviceValidationError(Exception):
    """Raised when device input fails validation or a uniqueness check."""


class DeviceService:
    """Device operations scoped to one company.

    Attributes:
        session: The active database session.
        company_id: The company this service operates within.
        actor_user_id: The user performing these operations, recorded
            on every audit log entry; ``None`` for a scheduled/
            automatic sync.
        device_manager: Resolves a :class:`~models.device.Device` to a
            live protocol connector.
    """

    def __init__(
        self,
        session: Session,
        *,
        company_id: int,
        actor_user_id: int | None = None,
        device_manager: DeviceManager | None = None,
    ) -> None:
        """Create a device service bound to one session and company.

        Args:
            session: The active database session.
            company_id: The company to operate within.
            actor_user_id: The acting user's id, for audit attribution.
            device_manager: Custom device manager, primarily for
                injecting a fake connector in tests; defaults to a new
                :class:`~devices.device_manager.DeviceManager`.
        """
        self.session = session
        self.company_id = company_id
        self.actor_user_id = actor_user_id
        self.device_repo = DeviceRepository(session, company_id=company_id)
        self.employee_repo = EmployeeRepository(session, company_id=company_id)
        self.punch_repo = AttendancePunchRepository(session, company_id=company_id)
        self.branch_repo = BranchRepository(session, company_id=company_id)
        self.audit_repo = AuditLogRepository(session)
        self.device_manager = device_manager or DeviceManager()

    def test_connection(self, device: Device) -> bool:
        """Test connectivity to a device and update its recorded status.

        Args:
            device: The device to test (must belong to this service's
                company).

        Returns:
            ``True`` if the connection succeeded.
        """
        connector = self.device_manager.get_connector(device)
        try:
            success = connector.test_connection()
        except Exception as exc:
            device.mark_error()
            self.session.flush()
            logger.warning(
                "Device {name} connection test failed: {error}",
                name=device.name,
                error=str(exc),
            )
            return False
        finally:
            connector.disconnect()

        if success:
            device.mark_online()
        else:
            device.mark_offline()
        self.session.flush()
        return success

    def sync_attendance_logs(self, device: Device) -> list[AttendancePunch]:
        """Download and persist a device's attendance logs.

        Skips any log entry whose device user reference does not match
        a known employee, and skips any ``(employee, punch_time)`` pair
        already imported for this device — safe to call repeatedly
        (e.g. on a scheduled interval) without creating duplicates.

        Args:
            device: The device to sync (must belong to this service's
                company).

        Returns:
            The newly created punches (already-imported and unmapped
            entries are not included).

        Raises:
            Exception: Whatever the underlying connector raises on a
                communication failure, after marking the device's
                status as errored.
        """
        connector = self.device_manager.get_connector(device)
        try:
            raw_logs = connector.fetch_attendance_logs()
        except Exception as exc:
            device.mark_error()
            self.session.flush()
            logger.error(
                "Device {name} attendance sync failed: {error}",
                name=device.name,
                error=str(exc),
            )
            raise
        finally:
            connector.disconnect()

        if not raw_logs:
            device.record_sync()
            self.session.flush()
            return []

        earliest = min(log.punch_time for log in raw_logs)
        latest = max(log.punch_time for log in raw_logs)
        already_imported = {
            (punch.employee_id, punch.punch_time)
            for punch in self.punch_repo.list_for_device_between(device.id, earliest, latest)
        }

        created: list[AttendancePunch] = []
        unmapped_count = 0
        for raw_log in raw_logs:
            employee = self.employee_repo.get_by_employee_number(
                raw_log.device_user_reference
            )
            if employee is None:
                unmapped_count += 1
                continue

            key = (employee.id, raw_log.punch_time)
            if key in already_imported:
                continue

            punch = AttendancePunch(
                company_id=self.company_id,
                employee_id=employee.id,
                device_id=device.id,
                punch_type=raw_log.punch_type,
                source=AttendanceSource.DEVICE,
                punch_time=raw_log.punch_time,
                device_user_reference=raw_log.device_user_reference,
            )
            self.punch_repo.add(punch)
            created.append(punch)
            already_imported.add(key)

        device.record_sync()
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.DEVICE_SYNC,
                entity_type="Device",
                entity_id=device.id,
                description=(
                    f"Synced {len(created)} attendance log(s) from device "
                    f"{device.name!r} ({unmapped_count} unmapped user "
                    f"reference(s) skipped)."
                ),
            )
        )
        return created

    def push_employee_to_device(self, device: Device, employee: Employee) -> None:
        """Enroll one employee as a device user (upload, not download).

        Args:
            device: The device to push to.
            employee: The employee to enroll; their
                :attr:`~models.employee.Employee.employee_number` is
                used as the device user reference.
        """
        connector = self.device_manager.get_connector(device)
        try:
            connector.push_users(
                [
                    RawDeviceUser(
                        device_user_reference=employee.employee_number,
                        name=employee.full_name,
                    )
                ]
            )
        finally:
            connector.disconnect()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.DEVICE_SYNC,
                entity_type="Device",
                entity_id=device.id,
                description=(
                    f"Enrolled employee {employee.employee_number!r} on device "
                    f"{device.name!r}."
                ),
            )
        )

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
    ) -> Device:
        """Register a new device.

        Args:
            name: Friendly device name; unique within this company.
            protocol: Which :class:`~models.enums.DeviceProtocol` the
                device speaks.
            host: IP address or hostname.
            port: Connection port.
            branch_id: The branch this device is installed at, if any.
            communication_key: Device-level shared secret, if any (see
                :attr:`~models.device.Device.communication_key`).
            timeout_seconds: Per-device connection timeout override.
            notes: Free-form notes.

        Returns:
            The newly created, persisted device.

        Raises:
            DeviceValidationError: If ``name`` fails length validation,
                ``host``/``port`` fail format validation, or the
                ``name``/``(host, port)`` combination is already in use
                in this company.
        """
        if not is_within_length(name, minimum=2, maximum=150):
            raise DeviceValidationError("Device name must be 2-150 characters.")
        if not is_valid_host(host):
            raise DeviceValidationError(f"Invalid device host: {host!r}.")
        if not is_valid_port(port):
            raise DeviceValidationError(f"Invalid device port: {port!r}.")
        if self.device_repo.get_by_name(name) is not None:
            raise DeviceValidationError(f"Device name {name!r} is already in use.")
        self._validate_branch(branch_id)

        device = Device(
            company_id=self.company_id,
            branch_id=branch_id,
            name=name,
            protocol=protocol,
            host=host,
            port=port,
            communication_key=communication_key,
            timeout_seconds=timeout_seconds,
            notes=notes,
            created_by_id=self.actor_user_id,
        )
        self.device_repo.add(device)

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.CREATE,
                entity_type="Device",
                entity_id=device.id,
                description=f"Registered device {name!r} ({protocol.value}) at {host}:{port}.",
            )
        )
        return device

    def update_device(self, device: Device, **fields: Any) -> Device:
        """Update an existing device's editable fields.

        Args:
            device: The device to update (must belong to this
                service's company).
            **fields: Any subset of ``name``/``host``/``port``/
                ``branch_id``/``communication_key``/``is_active``/
                ``timeout_seconds``/``notes``.

        Returns:
            The updated device.

        Raises:
            DeviceValidationError: If a provided field fails
                validation, or a provided ``name`` collides with a
                different device in this company.
        """
        if device.company_id != self.company_id:
            raise DeviceValidationError("This device does not belong to the current company.")
        if "name" in fields:
            if not is_within_length(str(fields["name"]), minimum=2, maximum=150):
                raise DeviceValidationError("Device name must be 2-150 characters.")
            existing = self.device_repo.get_by_name(str(fields["name"]))
            if existing is not None and existing.id != device.id:
                raise DeviceValidationError(f"Device name {fields['name']!r} is already in use.")
        if "host" in fields and not is_valid_host(str(fields["host"])):
            raise DeviceValidationError(f"Invalid device host: {fields['host']!r}.")
        if "port" in fields and not is_valid_port(int(fields["port"])):
            raise DeviceValidationError(f"Invalid device port: {fields['port']!r}.")
        if "branch_id" in fields:
            self._validate_branch(fields["branch_id"])

        device.update_from_dict(fields, allowed_fields=_UPDATABLE_FIELDS)
        device.updated_by_id = self.actor_user_id
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="Device",
                entity_id=device.id,
                description=f"Updated device {device.name!r}.",
                changes={key: str(value) for key, value in fields.items()},
            )
        )
        return device

    def delete_device(self, device: Device) -> None:
        """Soft-delete a device.

        Args:
            device: The device to remove from active views.
        """
        self.device_repo.delete(device)
        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.DELETE,
                entity_type="Device",
                entity_id=device.id,
                description=f"Deleted device {device.name!r}.",
            )
        )

    def list_devices(self, *, active_only: bool = False) -> list[Device]:
        """List this company's devices.

        Args:
            active_only: Restrict to devices eligible for scheduled
                sync jobs.

        Returns:
            Matching devices.
        """
        if active_only:
            return self.device_repo.list_active()
        return self.device_repo.list_all()

    def _validate_branch(self, branch_id: int | None) -> None:
        """Verify a branch id resolves to a branch in this company."""
        if branch_id is None:
            return
        if self.branch_repo.get_by_id(branch_id) is None:
            raise DeviceValidationError(
                f"Branch {branch_id!r} was not found in this company."
            )

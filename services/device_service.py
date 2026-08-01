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

from sqlalchemy.orm import Session

from devices.device_interface import RawDeviceUser
from devices.device_manager import DeviceManager
from models.attendance import AttendancePunch
from models.audit_log import AuditLog
from models.device import Device
from models.employee import Employee
from models.enums import AttendanceSource, AuditAction
from repositories.attendance_repository import AttendancePunchRepository
from repositories.audit_log_repository import AuditLogRepository
from repositories.device_repository import DeviceRepository
from repositories.employee_repository import EmployeeRepository
from utils.logger import logger


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

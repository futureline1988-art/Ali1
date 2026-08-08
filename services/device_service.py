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

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from devices.deli_es172_device import (
    ENROLLMENT_KIND_FACE,
    ENROLLMENT_KIND_FINGERPRINT,
    ENROLLMENT_KIND_PALM,
    JOB_STATE_FAILED,
    JOB_STATE_PENDING,
    JOB_STATE_SUCCEEDED,
    DeliEnrollmentJob,
)
from devices.device_interface import (
    ConnectionTestResult,
    DeviceCapabilities,
    DeviceConnectionError,
    RawDeviceUser,
)
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
from services.employee_service import EmployeeService, EmployeeValidationError
from utils.logger import logger
from utils.validators import is_valid_host, is_valid_port, is_within_length

_UPDATABLE_FIELDS = frozenset(
    {"name", "host", "port", "branch_id", "communication_key", "is_active", "timeout_seconds", "notes"}
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DeviceValidationError(Exception):
    """Raised when device input fails validation or a uniqueness check."""


class FaceEnrollmentOutcome(str, Enum):
    """Why a face-enrollment step ended the way it did.

    Deliberately returned as data (see :class:`FaceEnrollmentResult`)
    rather than raised as distinct exception types — a controller's
    generic exception boundary
    (:meth:`~controllers.base_controller.BaseController._run`) would
    otherwise collapse every one of these into one indistinguishable
    string, but the UI genuinely needs to react differently to each
    (see ``ui/employees.py``'s face-enrollment dialog).
    """

    UNSUPPORTED_DEVICE = "unsupported_device"
    DISCONNECTED = "disconnected"
    INVALID_EMPLOYEE_NUMBER = "invalid_employee_number"
    STARTED = "started"
    CONFIRMED = "confirmed"
    NOT_DETECTED = "not_detected"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True)
class FaceEnrollmentSession:
    """State the caller must hold onto between :meth:`DeviceService.begin_face_enrollment`
    and :meth:`DeviceService.confirm_face_enrollment` — the UI passes
    this back in once the employee has completed (or the operator has
    cancelled) enrollment on the physical device.

    Attributes:
        device_id: The device enrollment was started on.
        employee_id: The employee enrollment was started for.
        before_face_count: The device's device-wide enrolled-face count
            captured right before enrollment started — the only signal
            this project's ZKTeco integration can use to detect that
            *something* new was enrolled (see
            :meth:`DeviceService.confirm_face_enrollment`'s own
            docstring for why this can never be attributed to a
            specific employee by the device itself).
        started_at: When enrollment was started.
    """

    device_id: int
    employee_id: int
    before_face_count: int | None
    started_at: datetime


@dataclass(frozen=True)
class FaceEnrollmentResult:
    """The outcome of one face-enrollment step, with bilingual user-facing messages.

    Attributes:
        outcome: Which of :class:`FaceEnrollmentOutcome` this is.
        message_ar: Arabic message to show the operator.
        message_en: English message, for logs.
        session: Present only when :attr:`outcome` is :attr:`~FaceEnrollmentOutcome.STARTED`
            — the caller must hold onto it and pass it back into
            :meth:`DeviceService.confirm_face_enrollment`.
    """

    outcome: FaceEnrollmentOutcome
    message_ar: str
    message_en: str
    session: FaceEnrollmentSession | None = None


_DELI_KIND_LABELS_AR = {
    ENROLLMENT_KIND_FACE: "بصمة الوجه",
    ENROLLMENT_KIND_FINGERPRINT: "بصمة الإصبع",
    ENROLLMENT_KIND_PALM: "بصمة الكف",
}


class DeliEnrollmentOutcome(str, Enum):
    """Why a DELI ES172 on-device enrollment step ended the way it did.

    Deliberately a distinct enum from :class:`FaceEnrollmentOutcome`:
    DELI's workflow is a real job/template lifecycle (started -> pending
    -> succeeded/failed -> attached), never the ZKTeco device-wide-
    count heuristic that enum's members describe.
    """

    UNSUPPORTED_DEVICE = "unsupported_device"
    DISCONNECTED = "disconnected"
    STARTED = "started"
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ATTACHED = "attached"


@dataclass(frozen=True)
class DeliEnrollmentResult:
    """The outcome of one DELI ES172 biometric-enrollment step.

    Attributes:
        outcome: Which of :class:`DeliEnrollmentOutcome` this is.
        message_ar: Arabic message to show the operator.
        message_en: English message, for logs.
        job: The current job state, present for every outcome except
            :attr:`~DeliEnrollmentOutcome.UNSUPPORTED_DEVICE` and
            :attr:`~DeliEnrollmentOutcome.DISCONNECTED` -- the caller
            must hold onto it and pass it back into
            :meth:`DeviceService.poll_deli_enrollment_job`/
            :meth:`DeviceService.confirm_deli_enrollment`/
            :meth:`DeviceService.cancel_deli_enrollment`.
    """

    outcome: DeliEnrollmentOutcome
    message_ar: str
    message_en: str
    job: DeliEnrollmentJob | None = None


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

    def test_connection(self, device: Device) -> ConnectionTestResult:
        """Test connectivity to a device, update its recorded status, and diagnose any failure.

        Validates the address format locally first (no attempt is made
        to reach a syntactically invalid host), then attempts a real
        connection through :meth:`~devices.device_interface.DeviceConnector.test_connection_detailed`,
        which classifies the failure precisely (network unreachable,
        port closed, wrong communication key, timeout, unsupported
        protocol) rather than collapsing every reason into one generic
        message. On success, the device's reported model/serial/
        firmware are persisted onto ``device`` (columns that exist but
        are otherwise never populated).

        Args:
            device: The device to test (must belong to this service's
                company).

        Returns:
            The diagnostic :class:`~devices.device_interface.ConnectionTestResult`.
        """
        if not is_valid_host(device.host):
            device.mark_error()
            self.session.flush()
            return ConnectionTestResult(success=False, message_ar="عنوان IP غير صحيح.")
        if not is_valid_port(device.port):
            device.mark_error()
            self.session.flush()
            return ConnectionTestResult(
                success=False, message_ar=f"المنفذ {device.port} غير صحيح."
            )

        connector = self.device_manager.get_connector(device)
        try:
            result = connector.test_connection_detailed()
        except Exception as exc:  # noqa: BLE001 - any unexpected connector failure is still a diagnostic result
            device.mark_error()
            self.session.flush()
            logger.warning(
                "Device {name} connection test failed: {error}",
                name=device.name,
                error=str(exc),
            )
            return ConnectionTestResult(
                success=False, message_ar="تعذر الاتصال بالجهاز.", detail=str(exc)
            )
        finally:
            connector.disconnect()

        if result.success:
            device.mark_online()
            if result.capabilities is not None:
                if result.capabilities.serial_number:
                    device.serial_number = result.capabilities.serial_number
                if result.capabilities.device_model:
                    device.device_model = result.capabilities.device_model
                if result.capabilities.firmware_version:
                    device.firmware_version = result.capabilities.firmware_version
        else:
            device.mark_offline()
        self.session.flush()
        return result

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

    def pull_employees_from_device(self, device: Device) -> list[Employee]:
        """Download every user enrolled on the device and import unmatched ones as employees.

        The download counterpart to :meth:`push_employee_to_device`.
        Matches each device user against an existing employee by
        :attr:`~models.employee.Employee.employee_number` (the same
        convention every other device-mapping operation uses); a
        device user whose reference does not match any existing
        employee's number is created as a new, minimal employee record
        (only ``employee_number``/``full_name`` are known from the
        device — every other field an administrator can fill in
        afterward from the Employees screen). A device user that
        already matches an employee is left untouched, never
        overwritten.

        Args:
            device: The device to pull from (must belong to this
                service's company).

        Returns:
            The newly created employees (already-matched device users
            are not included).

        Raises:
            Exception: Whatever the underlying connector raises on a
                communication failure, after marking the device's
                status as errored.
        """
        connector = self.device_manager.get_connector(device)
        try:
            raw_users = connector.fetch_users()
        except Exception as exc:
            device.mark_error()
            self.session.flush()
            logger.error(
                "Device {name} employee download failed: {error}",
                name=device.name,
                error=str(exc),
            )
            raise
        finally:
            connector.disconnect()

        employee_service = EmployeeService(
            self.session, company_id=self.company_id, actor_user_id=self.actor_user_id
        )
        employee_repo = EmployeeRepository(self.session, company_id=self.company_id)

        created: list[Employee] = []
        for raw_user in raw_users:
            if not raw_user.device_user_reference:
                continue
            if employee_repo.get_by_employee_number(raw_user.device_user_reference) is not None:
                continue
            try:
                employee = employee_service.create_employee(
                    employee_number=raw_user.device_user_reference,
                    full_name=raw_user.name or raw_user.device_user_reference,
                )
            except EmployeeValidationError as exc:
                logger.warning(
                    "Skipped importing device user {reference!r} from {name}: {error}",
                    reference=raw_user.device_user_reference,
                    name=device.name,
                    error=str(exc),
                )
                continue
            created.append(employee)

        device.record_sync()
        self.session.flush()

        if created:
            self.audit_repo.add(
                AuditLog(
                    company_id=self.company_id,
                    user_id=self.actor_user_id,
                    action=AuditAction.DEVICE_SYNC,
                    entity_type="Device",
                    entity_id=device.id,
                    description=(
                        f"Imported {len(created)} employee(s) from device {device.name!r}."
                    ),
                )
            )
        return created

    def get_device_capabilities(self, device: Device) -> DeviceCapabilities:
        """Read a device's identity, capacity, and biometric support, updating its status.

        Args:
            device: The device to query.

        Returns:
            The device's capabilities.

        Raises:
            DeviceConnectionError: If the device could not be reached.
        """
        connector = self.device_manager.get_connector(device)
        try:
            capabilities = connector.get_capabilities()
        except Exception:
            device.mark_error()
            self.session.flush()
            raise
        finally:
            connector.disconnect()

        device.mark_online()
        if capabilities.serial_number and not device.serial_number:
            device.serial_number = capabilities.serial_number
        self.session.flush()
        return capabilities

    def refresh_employee_biometric_status(self, device: Device, employee: Employee) -> Employee:
        """Refresh one employee's fingerprint-count/card-assignment/palm-status from a device.

        Never touches :attr:`~models.employee.Employee.face_enrolled`
        — that field is only ever set by
        :meth:`confirm_face_enrollment`/:meth:`confirm_deli_enrollment`/
        :meth:`reset_face_enrollment_status`. For ZKTeco/Hikvision this
        is because face status cannot be re-derived from a fresh
        device read at all (see :meth:`confirm_face_enrollment`'s own
        docstring); DELI *could* report it here (``UserBiometricStatus.face_registered``),
        but is deliberately left to :meth:`confirm_deli_enrollment` so
        every protocol's face status changes through exactly one
        method, not two.

        Args:
            device: The device to read from.
            employee: The employee to refresh; must have already been
                pushed to this device (see :meth:`push_employee_to_device`)
                for a meaningful, non-zero result.

        Returns:
            The updated employee.

        Raises:
            DeviceConnectionError: If the device could not be reached.
        """
        connector = self.device_manager.get_connector(device)
        try:
            status = connector.get_user_biometric_status(employee.employee_number)
        finally:
            connector.disconnect()

        employee.fingerprint_count = status.fingerprint_count
        employee.card_assigned = status.card_assigned
        employee.palm_registered = status.palm_registered
        employee.biometric_last_synced_at = _utc_now()
        self.session.flush()
        return employee

    def begin_face_enrollment(self, device: Device, employee: Employee) -> FaceEnrollmentResult:
        """Start the on-device face-enrollment workflow for one employee.

        This project's ZKTeco integration (``pyzk``) has no protocol
        command to remotely trigger face enrollment (verified directly
        against the installed library — see
        :mod:`devices.zkteco_device`'s own module docstring), so
        "starting" enrollment here means exactly two things: (1)
        pushing the employee's record to the device using their
        (already UID-stable — see
        :meth:`~devices.zkteco_device.ZKTecoConnector.push_users`)
        device user reference, and (2) capturing the device's current
        enrolled-face count as a baseline for
        :meth:`confirm_face_enrollment` to compare against later. The
        actual face capture only ever happens when a person walks up
        to the physical device and enrolls through its own on-device
        menu — this method cannot make that happen, only prepare for
        it and report that preparation succeeded.

        Args:
            device: The device to enroll on; must be a ZKTeco device
                that reports face support (see
                :meth:`get_device_capabilities`).
            employee: The employee to enroll; their
                :attr:`~models.employee.Employee.employee_number` must
                be numeric (ZKTeco device user IDs are numeric).

        Returns:
            :attr:`~FaceEnrollmentOutcome.UNSUPPORTED_DEVICE` if the
            device is not a face-capable ZKTeco device;
            :attr:`~FaceEnrollmentOutcome.INVALID_EMPLOYEE_NUMBER` if
            the employee number is not numeric;
            :attr:`~FaceEnrollmentOutcome.DISCONNECTED` if the device
            could not be reached; :attr:`~FaceEnrollmentOutcome.STARTED`
            (with a :class:`FaceEnrollmentSession` to pass into
            :meth:`confirm_face_enrollment`) on success.
        """
        if not device.uses_zkteco_protocol:
            return FaceEnrollmentResult(
                outcome=FaceEnrollmentOutcome.UNSUPPORTED_DEVICE,
                message_ar="بصمة الوجه غير مدعومة إلا على أجهزة ZKTeco.",
                message_en="Face enrollment is only supported on ZKTeco devices.",
            )
        if not employee.employee_number.isdigit():
            return FaceEnrollmentResult(
                outcome=FaceEnrollmentOutcome.INVALID_EMPLOYEE_NUMBER,
                message_ar=(
                    "يجب أن يتكون الرقم الوظيفي من أرقام فقط لدعم التسجيل على "
                    "جهاز البصمة."
                ),
                message_en="The employee number must be numeric to support the biometric device.",
            )

        connector = self.device_manager.get_connector(device)
        try:
            capabilities = connector.get_capabilities()
        except DeviceConnectionError as exc:
            connector.disconnect()
            device.mark_error()
            self.session.flush()
            return FaceEnrollmentResult(
                outcome=FaceEnrollmentOutcome.DISCONNECTED,
                message_ar="تعذر الاتصال بالجهاز.",
                message_en=f"Could not reach the device: {exc}",
            )

        if not capabilities.supports_face:
            connector.disconnect()
            return FaceEnrollmentResult(
                outcome=FaceEnrollmentOutcome.UNSUPPORTED_DEVICE,
                message_ar="هذا الجهاز لا يدعم تسجيل بصمة الوجه.",
                message_en="This device does not support face enrollment.",
            )

        try:
            connector.push_users(
                [
                    RawDeviceUser(
                        device_user_reference=employee.employee_number,
                        name=employee.full_name,
                    )
                ]
            )
        except DeviceConnectionError as exc:
            device.mark_error()
            self.session.flush()
            return FaceEnrollmentResult(
                outcome=FaceEnrollmentOutcome.DISCONNECTED,
                message_ar="تعذر إرسال بيانات الموظف إلى الجهاز.",
                message_en=f"Failed to push employee data to the device: {exc}",
            )
        finally:
            connector.disconnect()

        device.mark_online()
        self.session.flush()
        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.DEVICE_SYNC,
                entity_type="Employee",
                entity_id=employee.id,
                description=(
                    f"Started face-enrollment workflow for employee "
                    f"{employee.employee_number!r} on device {device.name!r}."
                ),
            )
        )
        return FaceEnrollmentResult(
            outcome=FaceEnrollmentOutcome.STARTED,
            message_ar=(
                "تم إرسال بيانات الموظف إلى الجهاز. يرجى من الموظف الوقوف أمام "
                "جهاز البصمة وإكمال تسجيل الوجه."
            ),
            message_en=(
                "Employee data sent to the device. Ask the employee to stand in "
                "front of the device and complete face enrollment there."
            ),
            session=FaceEnrollmentSession(
                device_id=device.id,
                employee_id=employee.id,
                before_face_count=capabilities.face_template_count,
                started_at=_utc_now(),
            ),
        )

    def check_face_enrollment_progress(self, device: Device) -> int | None:
        """Read the device's current enrolled-face count, for a non-blocking progress poll.

        Deliberately returns only a bare count (device-wide, exactly
        like :meth:`begin_face_enrollment`'s baseline) rather than any
        per-employee signal — no such signal exists (see
        :meth:`confirm_face_enrollment`'s own docstring). Callers
        should compare this against
        :attr:`FaceEnrollmentSession.before_face_count` themselves.

        Raises:
            DeviceConnectionError: If the device could not be reached.
        """
        connector = self.device_manager.get_connector(device)
        try:
            return connector.get_capabilities().face_template_count
        finally:
            connector.disconnect()

    def confirm_face_enrollment(
        self,
        device: Device,
        employee: Employee,
        enrollment_session: FaceEnrollmentSession,
        *,
        operator_confirmed: bool,
    ) -> FaceEnrollmentResult:
        """Verify and record the outcome of a face-enrollment attempt.

        "Verify" here means exactly what this project's ZKTeco
        integration can actually check: whether the device's
        device-wide enrolled-face count increased since
        :meth:`begin_face_enrollment` captured
        :attr:`~FaceEnrollmentSession.before_face_count`. ``pyzk`` has
        no per-employee face-enrollment query — a rising count is
        *consistent with* this employee's enrollment having succeeded,
        it does not *prove* it (another enrollment happening on the
        same device around the same time is indistinguishable). That
        is why this method requires ``operator_confirmed=True`` before
        it will actually set
        :attr:`~models.employee.Employee.face_enrolled` — the human
        who was watching the device is the only party who can
        genuinely attribute the new enrollment to this specific
        employee.

        Args:
            device: The device enrollment was attempted on.
            employee: The employee enrollment was attempted for.
            enrollment_session: The session
                :meth:`begin_face_enrollment` returned.
            operator_confirmed: Whether the operator has confirmed the
                newly-detected enrollment belongs to this employee.
                Ignored (no-op) if no new enrollment was actually
                detected.

        Returns:
            :attr:`~FaceEnrollmentOutcome.DISCONNECTED` if the device
            could not be reached; :attr:`~FaceEnrollmentOutcome.NOT_DETECTED`
            if the face count did not increase, or increased but the
            operator has not (yet) confirmed it;
            :attr:`~FaceEnrollmentOutcome.CONFIRMED` once recorded.
        """
        connector = self.device_manager.get_connector(device)
        try:
            capabilities = connector.get_capabilities()
        except DeviceConnectionError as exc:
            connector.disconnect()
            return FaceEnrollmentResult(
                outcome=FaceEnrollmentOutcome.DISCONNECTED,
                message_ar="تعذر الاتصال بالجهاز للتحقق من التسجيل.",
                message_en=f"Could not reach the device to verify enrollment: {exc}",
            )
        finally:
            connector.disconnect()

        after_count = capabilities.face_template_count
        before_count = enrollment_session.before_face_count
        detected = before_count is not None and after_count is not None and after_count > before_count

        if not detected:
            employee.biometric_last_verification_result = "not_detected"
            self.session.flush()
            return FaceEnrollmentResult(
                outcome=FaceEnrollmentOutcome.NOT_DETECTED,
                message_ar="لم يتم اكتشاف تسجيل بصمة وجه جديد على الجهاز بعد.",
                message_en="No new face enrollment has been detected on the device yet.",
            )

        if not operator_confirmed:
            return FaceEnrollmentResult(
                outcome=FaceEnrollmentOutcome.NOT_DETECTED,
                message_ar=(
                    "تم اكتشاف تسجيل جديد على الجهاز. يرجى تأكيد أن هذا التسجيل "
                    "يخص هذا الموظف بالتحديد."
                ),
                message_en=(
                    "A new enrollment was detected on the device. Please confirm "
                    "it belongs to this specific employee."
                ),
            )

        employee.face_enrolled = True
        employee.face_enrolled_device_id = device.id
        employee.face_enrolled_at = _utc_now()
        employee.biometric_last_verification_result = "confirmed"
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.DEVICE_SYNC,
                entity_type="Employee",
                entity_id=employee.id,
                description=(
                    f"Confirmed face enrollment for employee "
                    f"{employee.employee_number!r} on device {device.name!r}."
                ),
            )
        )
        return FaceEnrollmentResult(
            outcome=FaceEnrollmentOutcome.CONFIRMED,
            message_ar="تم تأكيد تسجيل بصمة الوجه بنجاح.",
            message_en="Face enrollment confirmed successfully.",
        )

    def cancel_face_enrollment(self, employee: Employee) -> None:
        """Record that an in-progress face-enrollment attempt was cancelled.

        Args:
            employee: The employee whose enrollment attempt was
                cancelled.
        """
        employee.biometric_last_verification_result = "cancelled"
        self.session.flush()

    def reset_face_enrollment_status(self, employee: Employee) -> Employee:
        """Locally clear an employee's cached face-enrollment status.

        This does **not** delete anything from the device — ``pyzk``
        has no command to delete a face template specifically (only
        an entire user, or a fingerprint template by slot); deleting
        the whole device user would also drop their fingerprint
        templates and attendance-log association, which this method
        deliberately avoids doing silently. Use this when the local
        record has drifted from reality (e.g. the face was removed
        directly on the device, or was mistakenly attributed to this
        employee) and needs to be corrected here without touching the
        device at all.

        Args:
            employee: The employee to reset.

        Returns:
            The updated employee.
        """
        employee.face_enrolled = False
        employee.face_enrolled_device_id = None
        employee.face_enrolled_at = None
        employee.biometric_last_verification_result = "local_reset"
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.DEVICE_SYNC,
                entity_type="Employee",
                entity_id=employee.id,
                description=(
                    f"Locally reset face-enrollment status for employee "
                    f"{employee.employee_number!r} (device template, if any, "
                    f"is unchanged -- unsupported by this project's ZKTeco library)."
                ),
            )
        )
        return employee

    # ------------------------------------------------------------------
    # DELI ES172 on-device biometric enrollment
    #
    # Unlike the ZKTeco heuristic above, the official DELI SDK protocol
    # gives a definitive job/template result (BeginEnrollFace/BeginEnrollFp/
    # BeginEnrollPalm -> QueryJobStatus -> SetUserInfo) -- see
    # devices.deli_es172_device's own module docstring for the exact
    # documented commands this orchestrates. No device-wide count
    # heuristic, no operator "was this really them" confirmation step:
    # the device itself reports success/failure and hands back the
    # captured template.
    # ------------------------------------------------------------------

    _DELI_KIND_TO_CAPABILITY_ATTR = {
        ENROLLMENT_KIND_FACE: "supports_face",
        ENROLLMENT_KIND_FINGERPRINT: "supports_fingerprint",
        ENROLLMENT_KIND_PALM: "supports_palm",
    }

    def begin_deli_enrollment(
        self, device: Device, employee: Employee, *, kind: str
    ) -> DeliEnrollmentResult:
        """Start on-device biometric capture for one employee on a DELI ES172.

        Pushes the employee to the device first (so the subsequent
        :meth:`confirm_deli_enrollment`'s ``SetUserInfo`` attaches the
        template to a real, named user rather than creating a bare
        one), then issues the documented ``BeginEnrollFace``/
        ``BeginEnrollFp``/``BeginEnrollPalm`` command. The device then
        waits for the employee to present their biometric at the
        physical unit -- this call cannot make that happen, only start
        the job and report that it started.

        Args:
            device: Must be a DELI ES172 device that reports support
                for ``kind``.
            employee: The employee to enroll.
            kind: One of ``devices.deli_es172_device.ENROLLMENT_KIND_FACE``/
                ``ENROLLMENT_KIND_FINGERPRINT``/``ENROLLMENT_KIND_PALM``.

        Returns:
            :attr:`~DeliEnrollmentOutcome.UNSUPPORTED_DEVICE` if this
            is not a DELI ES172 device or it does not report ``kind``
            support; :attr:`~DeliEnrollmentOutcome.DISCONNECTED` if the
            device could not be reached; :attr:`~DeliEnrollmentOutcome.STARTED`
            (with the new job) on success.
        """
        kind_label = _DELI_KIND_LABELS_AR[kind]
        if device.protocol != DeviceProtocol.DELI_ES172:
            return DeliEnrollmentResult(
                outcome=DeliEnrollmentOutcome.UNSUPPORTED_DEVICE,
                message_ar=f"تسجيل {kind_label} من التطبيق مدعوم فقط على أجهزة DELI ES172.",
                message_en="On-device biometric enrollment is only supported on DELI ES172 devices.",
            )

        connector = self.device_manager.get_connector(device)
        try:
            capabilities = connector.get_capabilities()
        except DeviceConnectionError as exc:
            connector.disconnect()
            device.mark_error()
            self.session.flush()
            return DeliEnrollmentResult(
                outcome=DeliEnrollmentOutcome.DISCONNECTED,
                message_ar="تعذر الاتصال بالجهاز.",
                message_en=f"Could not reach the device: {exc}",
            )

        if not getattr(capabilities, self._DELI_KIND_TO_CAPABILITY_ATTR[kind]):
            connector.disconnect()
            return DeliEnrollmentResult(
                outcome=DeliEnrollmentOutcome.UNSUPPORTED_DEVICE,
                message_ar=f"هذا الجهاز لا يدعم تسجيل {kind_label}.",
                message_en=f"This device does not support {kind} enrollment.",
            )

        try:
            connector.push_users(
                [
                    RawDeviceUser(
                        device_user_reference=employee.employee_number,
                        name=employee.full_name,
                    )
                ]
            )
            if kind == ENROLLMENT_KIND_FACE:
                job = connector.begin_face_enrollment()
            elif kind == ENROLLMENT_KIND_FINGERPRINT:
                job = connector.begin_fingerprint_enrollment()
            else:
                job = connector.begin_palm_enrollment()
        except DeviceConnectionError as exc:
            device.mark_error()
            self.session.flush()
            return DeliEnrollmentResult(
                outcome=DeliEnrollmentOutcome.DISCONNECTED,
                message_ar="تعذر بدء عملية التسجيل على الجهاز.",
                message_en=f"Failed to start enrollment on the device: {exc}",
            )
        finally:
            connector.disconnect()

        device.mark_online()
        self.session.flush()
        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.DEVICE_SYNC,
                entity_type="Employee",
                entity_id=employee.id,
                description=(
                    f"Started DELI {kind} enrollment job {job.job_id} for employee "
                    f"{employee.employee_number!r} on device {device.name!r}."
                ),
            )
        )
        return DeliEnrollmentResult(
            outcome=DeliEnrollmentOutcome.STARTED,
            message_ar=(
                f"تم بدء عملية التسجيل على الجهاز. يرجى من الموظف الوقوف أمام "
                f"الجهاز لإكمال تسجيل {kind_label}."
            ),
            message_en=(
                f"Enrollment started on the device. Ask the employee to stand in "
                f"front of it to complete {kind} capture."
            ),
            job=job,
        )

    def poll_deli_enrollment_job(self, device: Device, job: DeliEnrollmentJob) -> DeliEnrollmentResult:
        """Check one DELI enrollment job's current state via ``QueryJobStatus``.

        Non-blocking -- the caller (typically a UI timer) is expected
        to call this repeatedly until the outcome is no longer
        :attr:`~DeliEnrollmentOutcome.PENDING`.

        Args:
            device: The device the job is running on.
            job: A job from :meth:`begin_deli_enrollment` (or an
                earlier poll).

        Returns:
            :attr:`~DeliEnrollmentOutcome.DISCONNECTED` if the device
            could not be reached; :attr:`~DeliEnrollmentOutcome.PENDING`
            while capture is still in progress;
            :attr:`~DeliEnrollmentOutcome.FAILED` if capture failed or
            was cancelled; :attr:`~DeliEnrollmentOutcome.SUCCEEDED`
            (with the captured template in the returned job) once
            capture completes.
        """
        kind_label = _DELI_KIND_LABELS_AR[job.kind]
        connector = self.device_manager.get_connector(device)
        try:
            updated = connector.query_enrollment_job(job)
        except DeviceConnectionError as exc:
            connector.disconnect()
            return DeliEnrollmentResult(
                outcome=DeliEnrollmentOutcome.DISCONNECTED,
                message_ar="تعذر الاتصال بالجهاز لمتابعة حالة التسجيل.",
                message_en=f"Could not reach the device to check enrollment status: {exc}",
                job=job,
            )
        finally:
            connector.disconnect()

        if updated.state == JOB_STATE_PENDING:
            return DeliEnrollmentResult(
                outcome=DeliEnrollmentOutcome.PENDING,
                message_ar=f"بانتظار قيام الموظف بإكمال تسجيل {kind_label} على الجهاز...",
                message_en="Waiting for the employee to complete capture on the device...",
                job=updated,
            )
        if updated.state == JOB_STATE_FAILED:
            return DeliEnrollmentResult(
                outcome=DeliEnrollmentOutcome.FAILED,
                message_ar=f"فشلت عملية تسجيل {kind_label} أو تم إلغاؤها على الجهاز.",
                message_en="Capture failed or was cancelled on the device.",
                job=updated,
            )
        return DeliEnrollmentResult(
            outcome=DeliEnrollmentOutcome.SUCCEEDED,
            message_ar=f"تم التقاط {kind_label} بنجاح على الجهاز. اضغط لحفظها لهذا الموظف.",
            message_en="Capture succeeded on the device. Confirm to save it for this employee.",
            job=updated,
        )

    def confirm_deli_enrollment(
        self, device: Device, employee: Employee, job: DeliEnrollmentJob
    ) -> DeliEnrollmentResult:
        """Attach a succeeded DELI enrollment job's template to an employee.

        Sends the documented ``SetUserInfo`` command (via
        :meth:`~devices.deli_es172_device.DeliES172Connector.set_user_face_template`/
        ``set_user_fingerprint_template``/``set_user_palm_template``) so
        the captured template on the device is genuinely associated
        with this employee's device user id -- capture alone
        (:meth:`begin_deli_enrollment`/:meth:`poll_deli_enrollment_job`)
        never does this by itself, per the document's own two-step
        capture-then-attach design.

        Args:
            device: The device the job ran on.
            employee: The employee to attach the template to.
            job: A job whose :attr:`~devices.deli_es172_device.DeliEnrollmentJob.state`
                is ``JOB_STATE_SUCCEEDED`` (from :meth:`poll_deli_enrollment_job`).

        Returns:
            :attr:`~DeliEnrollmentOutcome.FAILED` if the job has not
            actually succeeded; :attr:`~DeliEnrollmentOutcome.DISCONNECTED`
            if the device could not be reached;
            :attr:`~DeliEnrollmentOutcome.ATTACHED` once saved.
        """
        kind_label = _DELI_KIND_LABELS_AR[job.kind]
        if job.state != JOB_STATE_SUCCEEDED or not job.template_data:
            return DeliEnrollmentResult(
                outcome=DeliEnrollmentOutcome.FAILED,
                message_ar=f"لا توجد بيانات {kind_label} ملتقطة لحفظها.",
                message_en="No captured template is available to save.",
                job=job,
            )

        connector = self.device_manager.get_connector(device)
        try:
            if job.kind == ENROLLMENT_KIND_FACE:
                connector.set_user_face_template(employee.employee_number, job.template_data)
            elif job.kind == ENROLLMENT_KIND_FINGERPRINT:
                connector.set_user_fingerprint_template(employee.employee_number, job.template_data)
            else:
                connector.set_user_palm_template(employee.employee_number, job.template_data)
        except DeviceConnectionError as exc:
            device.mark_error()
            self.session.flush()
            return DeliEnrollmentResult(
                outcome=DeliEnrollmentOutcome.DISCONNECTED,
                message_ar=f"تعذر حفظ بيانات {kind_label} على الجهاز.",
                message_en=f"Failed to save the template on the device: {exc}",
                job=job,
            )
        finally:
            connector.disconnect()

        if job.kind == ENROLLMENT_KIND_FACE:
            employee.face_enrolled = True
            employee.face_enrolled_device_id = device.id
            employee.face_enrolled_at = _utc_now()
            employee.biometric_last_verification_result = "confirmed"
        elif job.kind == ENROLLMENT_KIND_PALM:
            employee.palm_registered = True
        employee.biometric_last_synced_at = _utc_now()
        device.mark_online()
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.DEVICE_SYNC,
                entity_type="Employee",
                entity_id=employee.id,
                description=(
                    f"Attached DELI {job.kind} template (job {job.job_id}) to employee "
                    f"{employee.employee_number!r} on device {device.name!r}."
                ),
            )
        )
        return DeliEnrollmentResult(
            outcome=DeliEnrollmentOutcome.ATTACHED,
            message_ar=f"تم حفظ {kind_label} للموظف بنجاح.",
            message_en=f"{job.kind} template saved for the employee.",
            job=job,
        )

    def cancel_deli_enrollment(self, device: Device, job: DeliEnrollmentJob) -> DeliEnrollmentResult:
        """Cancel an in-progress DELI enrollment job via the documented ``CancelJob`` command.

        Args:
            device: The device the job is running on.
            job: The job to cancel.

        Returns:
            :attr:`~DeliEnrollmentOutcome.DISCONNECTED` if the device
            could not be reached; :attr:`~DeliEnrollmentOutcome.CANCELLED`
            once cancelled.
        """
        connector = self.device_manager.get_connector(device)
        try:
            connector.cancel_enrollment_job(job)
        except DeviceConnectionError as exc:
            connector.disconnect()
            return DeliEnrollmentResult(
                outcome=DeliEnrollmentOutcome.DISCONNECTED,
                message_ar="تعذر إلغاء عملية التسجيل على الجهاز.",
                message_en=f"Failed to cancel enrollment on the device: {exc}",
                job=job,
            )
        finally:
            connector.disconnect()

        return DeliEnrollmentResult(
            outcome=DeliEnrollmentOutcome.CANCELLED,
            message_ar="تم إلغاء عملية التسجيل.",
            message_en="Enrollment cancelled.",
            job=job,
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

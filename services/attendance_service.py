"""Attendance service: manual/device punch recording and daily aggregation.

:meth:`AttendanceService.compute_daily_attendance` is where this
system's core promise — automatic late/overtime/working-hours
calculation — is actually implemented. It resolves, in order:

1. Is the day a company holiday? -> ``HOLIDAY``, no punch comparison.
2. Does the employee have an approved leave covering this day? ->
   ``ON_LEAVE``.
3. Is this a non-working day under the employee's effective shift? ->
   ``WEEKEND``.
4. No punches recorded? -> ``ABSENT``.
5. Otherwise, compare the day's punches against the effective shift's
   start/end time (with its configured grace periods and overtime
   threshold) to compute late/early-leave/overtime/worked minutes.

"Effective shift" means whichever
:class:`~models.shift.EmployeeShiftAssignment` covered that specific
calendar day — not necessarily the employee's *current* shift — so
recomputing a past day after the employee has since changed shifts
still uses the shift that was actually in effect then.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from models.attendance import AttendancePunch, AttendanceRecord
from models.enums import AttendanceDayStatus, AttendanceSource, LeaveStatus, PunchType, Weekday
from models.shift import Shift
from repositories.attendance_repository import (
    AttendancePunchRepository,
    AttendanceRecordRepository,
)
from repositories.company_settings_repository import CompanySettingsRepository
from repositories.employee_repository import EmployeeRepository
from repositories.holiday_repository import HolidayRepository
from repositories.leave_repository import LeaveRequestRepository
from repositories.shift_repository import EmployeeShiftAssignmentRepository

_PYTHON_WEEKDAY_TO_ENUM = {
    0: Weekday.MONDAY,
    1: Weekday.TUESDAY,
    2: Weekday.WEDNESDAY,
    3: Weekday.THURSDAY,
    4: Weekday.FRIDAY,
    5: Weekday.SATURDAY,
    6: Weekday.SUNDAY,
}


def _weekday_for(work_date: date) -> Weekday:
    """Map a Python ``date`` to this project's :class:`~models.enums.Weekday`."""
    return _PYTHON_WEEKDAY_TO_ENUM[work_date.weekday()]


class AttendanceService:
    """Attendance operations scoped to one company.

    Attributes:
        session: The active database session.
        company_id: The company this service operates within.
    """

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create an attendance service bound to one session and company.

        Args:
            session: The active database session.
            company_id: The company to operate within.
        """
        self.session = session
        self.company_id = company_id
        self.record_repo = AttendanceRecordRepository(session, company_id=company_id)
        self.punch_repo = AttendancePunchRepository(session, company_id=company_id)
        self.assignment_repo = EmployeeShiftAssignmentRepository(
            session, company_id=company_id
        )
        self.holiday_repo = HolidayRepository(session, company_id=company_id)
        self.leave_repo = LeaveRequestRepository(session, company_id=company_id)
        self.employee_repo = EmployeeRepository(session, company_id=company_id)
        self.settings_repo = CompanySettingsRepository(session, company_id=company_id)

    def record_manual_punch(
        self,
        *,
        employee_id: int,
        punch_type: PunchType,
        punch_time: datetime | None = None,
        notes: str | None = None,
    ) -> AttendancePunch:
        """Record a manually-entered punch.

        Args:
            employee_id: The employee punching in/out.
            punch_type: Whether this is a check-in or check-out.
            punch_time: When the punch occurred; defaults to now (UTC).
            notes: Optional reason for the manual entry.

        Returns:
            The newly created, persisted punch.
        """
        punch = AttendancePunch(
            company_id=self.company_id,
            employee_id=employee_id,
            punch_type=punch_type,
            source=AttendanceSource.MANUAL,
            punch_time=punch_time or datetime.now(timezone.utc),
            notes=notes,
        )
        self.punch_repo.add(punch)
        return punch

    def import_device_punch(
        self,
        *,
        employee_id: int,
        device_id: int,
        punch_type: PunchType,
        punch_time: datetime,
        device_user_reference: str | None = None,
    ) -> AttendancePunch:
        """Record a punch imported from a biometric device.

        Args:
            employee_id: The employee the device log resolved to.
            device_id: The source device.
            punch_type: Whether this is a check-in or check-out.
            punch_time: When the punch occurred, per the device clock
                (UTC).
            device_user_reference: The raw user id as reported by the
                device, for troubleshooting.

        Returns:
            The newly created, persisted punch.
        """
        punch = AttendancePunch(
            company_id=self.company_id,
            employee_id=employee_id,
            device_id=device_id,
            punch_type=punch_type,
            source=AttendanceSource.DEVICE,
            punch_time=punch_time,
            device_user_reference=device_user_reference,
        )
        self.punch_repo.add(punch)
        return punch

    def get_effective_shift(self, employee_id: int, work_date: date) -> Shift | None:
        """Resolve which shift was in effect for an employee on a given day.

        Args:
            employee_id: The employee's id.
            work_date: The calendar day to resolve.

        Returns:
            The effective :class:`~models.shift.Shift`, or ``None`` if
            no assignment covered that day.
        """
        assignment = self.assignment_repo.get_effective_for_date(employee_id, work_date)
        return assignment.shift if assignment is not None else None

    def compute_daily_attendance(
        self, *, employee_id: int, work_date: date
    ) -> AttendanceRecord:
        """Aggregate one employee's punches for one day into an attendance record.

        Idempotent: recomputing an already-computed day updates the
        existing :class:`~models.attendance.AttendanceRecord` in place
        rather than creating a duplicate (the model's
        ``UNIQUE(company, employee, work_date)`` constraint would
        reject a duplicate anyway).

        Args:
            employee_id: The employee to compute attendance for.
            work_date: The calendar day to compute.

        Returns:
            The computed (created or updated) attendance record.
        """
        shift = self.get_effective_shift(employee_id, work_date)
        day_start_utc, day_end_utc = self._day_boundaries_utc(work_date)
        punches = self.punch_repo.list_for_employee_between(
            employee_id, day_start_utc, day_end_utc
        )

        if self.holiday_repo.is_holiday(work_date):
            result = _DayResult(status=AttendanceDayStatus.HOLIDAY)
        elif self._has_approved_leave(employee_id, work_date):
            result = _DayResult(status=AttendanceDayStatus.ON_LEAVE)
        elif shift is not None and not shift.is_working_day(_weekday_for(work_date)):
            result = _DayResult(status=AttendanceDayStatus.WEEKEND)
        elif not punches:
            result = _DayResult(status=AttendanceDayStatus.ABSENT)
        else:
            tz = self._get_company_timezone()
            result = self._compute_from_punches(punches, shift, work_date, tz)

        existing = self.record_repo.get_for_employee_and_date(employee_id, work_date)
        if existing is not None:
            existing.shift_id = shift.id if shift is not None else None
            existing.check_in_time = result.check_in
            existing.check_out_time = result.check_out
            existing.status = result.status
            existing.late_minutes = result.late_minutes
            existing.early_leave_minutes = result.early_leave_minutes
            existing.overtime_minutes = result.overtime_minutes
            existing.worked_minutes = result.worked_minutes
            record = existing
        else:
            record = AttendanceRecord(
                company_id=self.company_id,
                employee_id=employee_id,
                shift_id=shift.id if shift is not None else None,
                work_date=work_date,
                check_in_time=result.check_in,
                check_out_time=result.check_out,
                status=result.status,
                late_minutes=result.late_minutes,
                early_leave_minutes=result.early_leave_minutes,
                overtime_minutes=result.overtime_minutes,
                worked_minutes=result.worked_minutes,
            )
            self.record_repo.add(record)

        self.session.flush()
        for punch in punches:
            punch.attendance_record_id = record.id
        self.session.flush()
        return record

    def bulk_compute_for_date(self, work_date: date) -> list[AttendanceRecord]:
        """Compute attendance for every active employee on one day.

        The entry point a nightly scheduled job calls.

        Args:
            work_date: The calendar day to compute.

        Returns:
            One attendance record per active employee.
        """
        return [
            self.compute_daily_attendance(employee_id=employee.id, work_date=work_date)
            for employee in self.employee_repo.list_active()
        ]

    def _compute_from_punches(
        self,
        punches: list[AttendancePunch],
        shift: Shift | None,
        work_date: date,
        tz: ZoneInfo,
    ) -> "_DayResult":
        """Derive check-in/out times and late/overtime/worked minutes from punches.

        Args:
            punches: This day's raw punches (already filtered to one
                employee and one local calendar day).
            shift: The effective shift, if any.
            work_date: The local calendar day being computed.
            tz: The company's local timezone —
                :attr:`~models.shift.Shift.start_time`/``end_time`` are
                wall-clock times meant to be read in this timezone, not
                UTC, so scheduled start/end must be built with ``tz``
                and only then converted for comparison against the
                UTC-stored punch timestamps.
        """
        check_ins = [p.punch_time for p in punches if p.punch_type is PunchType.CHECK_IN]
        check_outs = [p.punch_time for p in punches if p.punch_type is PunchType.CHECK_OUT]
        check_in = min(check_ins) if check_ins else None
        check_out = max(check_outs) if check_outs else None

        worked_minutes = 0
        if check_in is not None and check_out is not None and check_out > check_in:
            worked_minutes = int((check_out - check_in).total_seconds() // 60)

        status = AttendanceDayStatus.PRESENT
        late_minutes = 0
        early_leave_minutes = 0
        overtime_minutes = 0

        if shift is not None and check_in is not None:
            scheduled_start = datetime.combine(
                work_date, shift.start_time, tzinfo=tz
            ).astimezone(timezone.utc)
            grace = timedelta(minutes=shift.grace_period_minutes)
            if check_in > scheduled_start + grace:
                late_minutes = int((check_in - scheduled_start).total_seconds() // 60)
                status = AttendanceDayStatus.LATE

        if shift is not None and check_out is not None:
            end_date = work_date + timedelta(days=1) if shift.spans_midnight else work_date
            scheduled_end = datetime.combine(
                end_date, shift.end_time, tzinfo=tz
            ).astimezone(timezone.utc)
            early_grace = timedelta(minutes=shift.early_leave_grace_minutes)
            if check_out < scheduled_end - early_grace:
                early_leave_minutes = int((scheduled_end - check_out).total_seconds() // 60)
                if status is AttendanceDayStatus.PRESENT:
                    status = AttendanceDayStatus.EARLY_LEAVE
            elif check_out > scheduled_end:
                overtime_threshold = timedelta(minutes=shift.overtime_threshold_minutes)
                if check_out - scheduled_end > overtime_threshold:
                    overtime_minutes = int((check_out - scheduled_end).total_seconds() // 60)

        return _DayResult(
            status=status,
            check_in=check_in,
            check_out=check_out,
            late_minutes=late_minutes,
            early_leave_minutes=early_leave_minutes,
            overtime_minutes=overtime_minutes,
            worked_minutes=worked_minutes,
        )

    def _has_approved_leave(self, employee_id: int, work_date: date) -> bool:
        """Whether an approved leave request covers ``work_date``."""
        requests = self.leave_repo.list_for_employee(employee_id)
        return any(
            request.status is LeaveStatus.APPROVED
            and request.start_date <= work_date <= request.end_date
            for request in requests
        )

    def _get_company_timezone(self) -> ZoneInfo:
        """Resolve this company's configured display/business timezone.

        Falls back to ``"Asia/Baghdad"`` if
        :class:`~models.company_settings.CompanySettings` has not been
        seeded for this company yet.
        """
        settings = self.settings_repo.get_for_company()
        tz_name = settings.timezone_name if settings is not None else "Asia/Baghdad"
        return ZoneInfo(tz_name)

    def _day_boundaries_utc(self, work_date: date) -> tuple[datetime, datetime]:
        """Compute a calendar day's [start, end) boundaries in UTC.

        Uses the company's local timezone (see
        :meth:`_get_company_timezone`) so "midnight" means local
        midnight, not UTC midnight — otherwise an employee working an
        evening shift near midnight local time could have their punches
        split across two different UTC calendar days.

        Args:
            work_date: The local calendar day.

        Returns:
            A ``(start, end)`` tuple of UTC datetimes spanning that
            local day.
        """
        tz = self._get_company_timezone()
        local_start = datetime.combine(work_date, time.min, tzinfo=tz)
        # One microsecond before next-day midnight: list_for_employee_between()
        # is inclusive on both ends, so using exact midnight here would let a
        # punch at that precise instant match both this day and the next one.
        local_end = (
            datetime.combine(work_date + timedelta(days=1), time.min, tzinfo=tz)
            - timedelta(microseconds=1)
        )
        return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


class _DayResult:
    """Internal value object: one day's computed attendance figures."""

    def __init__(
        self,
        *,
        status: AttendanceDayStatus,
        check_in: datetime | None = None,
        check_out: datetime | None = None,
        late_minutes: int = 0,
        early_leave_minutes: int = 0,
        overtime_minutes: int = 0,
        worked_minutes: int = 0,
    ) -> None:
        self.status = status
        self.check_in = check_in
        self.check_out = check_out
        self.late_minutes = late_minutes
        self.early_leave_minutes = early_leave_minutes
        self.overtime_minutes = overtime_minutes
        self.worked_minutes = worked_minutes

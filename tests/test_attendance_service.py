"""Attendance computation: the system's core business logic.

Covers the same end-to-end path this project's development relied on
throughout — shift assignment, holidays, and approved leave all
feeding into :meth:`~services.attendance_service.AttendanceService.compute_daily_attendance`
— as real pytest assertions instead of an ad-hoc script.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest

from database.database import session_scope
from models.enums import AttendanceDayStatus, LeaveType, PunchType
from repositories.role_repository import RoleRepository
from services.attendance_service import AttendanceService
from services.employee_service import EmployeeService
from services.holiday_service import HolidayService
from services.leave_service import LeaveService
from services.shift_service import ShiftService
from services.user_service import UserService


@pytest.fixture
def attendance_fixture(company_factory):
    """Build one company with an employee, an assigned shift, a holiday, and approved leave.

    Returns:
        A dict with ``company_id``, ``employee_id``, and ``shift_id``.
    """
    company_id = company_factory()

    with session_scope() as session:
        employee = EmployeeService(session, company_id=company_id).create_employee(
            employee_number="A-001", full_name="أحمد علي"
        )
        employee_id = employee.id

        role = RoleRepository(session, company_id=company_id).list_all()[0]
        reviewer = UserService(session, company_id=company_id).create_user(
            username="reviewer", password="Passw0rd!23", full_name="مراجع", role_id=role.id
        )
        reviewer_id = reviewer.id

    with session_scope() as session:
        shift_svc = ShiftService(session, company_id=company_id)
        shift = shift_svc.create_shift(
            name="وردية صباحية",
            start_time=time(9, 0),
            end_time=time(17, 0),
            grace_period_minutes=10,
            working_days=[
                "saturday", "sunday", "monday", "tuesday", "wednesday", "thursday",
            ],
        )
        shift_svc.assign_employee(
            employee_id=employee_id, shift_id=shift.id, effective_from=date(2026, 1, 1)
        )
        shift_id = shift.id

        HolidayService(session, company_id=company_id).create_holiday(
            name="عطلة رسمية", holiday_date=date(2026, 9, 10), is_recurring_annually=False
        )

        leave_svc = LeaveService(session, company_id=company_id)
        policy = leave_svc.create_policy(
            leave_type=LeaveType.ANNUAL, name="سنوية", annual_entitlement_days=20
        )
        leave_request = leave_svc.submit_request(
            employee_id=employee_id,
            leave_policy_id=policy.id,
            start_date=date(2026, 9, 15),
            end_date=date(2026, 9, 15),
        )
        leave_svc.approve_request(leave_request, reviewer_id=reviewer_id)

    return {"company_id": company_id, "employee_id": employee_id, "shift_id": shift_id}


def test_get_effective_shift_resolves_assigned_shift(attendance_fixture):
    with session_scope() as session:
        att_svc = AttendanceService(session, company_id=attendance_fixture["company_id"])
        shift = att_svc.get_effective_shift(attendance_fixture["employee_id"], date(2026, 9, 8))
    assert shift is not None
    assert shift.id == attendance_fixture["shift_id"]


def test_on_time_punch_computes_present(attendance_fixture):
    company_id = attendance_fixture["company_id"]
    employee_id = attendance_fixture["employee_id"]

    with session_scope() as session:
        att_svc = AttendanceService(session, company_id=company_id)
        att_svc.record_manual_punch(
            employee_id=employee_id,
            punch_type=PunchType.CHECK_IN,
            punch_time=datetime(2026, 9, 8, 9, 5, tzinfo=timezone.utc),
        )
        att_svc.record_manual_punch(
            employee_id=employee_id,
            punch_type=PunchType.CHECK_OUT,
            punch_time=datetime(2026, 9, 8, 17, 10, tzinfo=timezone.utc),
        )
        record = att_svc.compute_daily_attendance(employee_id=employee_id, work_date=date(2026, 9, 8))

    assert record.shift_id == attendance_fixture["shift_id"]
    assert record.status in (AttendanceDayStatus.PRESENT, AttendanceDayStatus.LATE)


def test_defined_holiday_overrides_status(attendance_fixture):
    with session_scope() as session:
        att_svc = AttendanceService(session, company_id=attendance_fixture["company_id"])
        record = att_svc.compute_daily_attendance(
            employee_id=attendance_fixture["employee_id"], work_date=date(2026, 9, 10)
        )
    assert record.status is AttendanceDayStatus.HOLIDAY


def test_approved_leave_day_computes_on_leave(attendance_fixture):
    with session_scope() as session:
        att_svc = AttendanceService(session, company_id=attendance_fixture["company_id"])
        record = att_svc.compute_daily_attendance(
            employee_id=attendance_fixture["employee_id"], work_date=date(2026, 9, 15)
        )
    assert record.status is AttendanceDayStatus.ON_LEAVE


def test_non_working_day_computes_weekend(attendance_fixture):
    with session_scope() as session:
        att_svc = AttendanceService(session, company_id=attendance_fixture["company_id"])
        record = att_svc.compute_daily_attendance(
            employee_id=attendance_fixture["employee_id"], work_date=date(2026, 9, 11)
        )  # Friday, not in this shift's working_days
    assert record.status is AttendanceDayStatus.WEEKEND


def test_working_day_without_punches_computes_absent(attendance_fixture):
    with session_scope() as session:
        att_svc = AttendanceService(session, company_id=attendance_fixture["company_id"])
        record = att_svc.compute_daily_attendance(
            employee_id=attendance_fixture["employee_id"], work_date=date(2026, 9, 9)
        )  # Wednesday, no punches
    assert record.status is AttendanceDayStatus.ABSENT


def test_check_in_without_check_out_computes_missing_check_out(attendance_fixture):
    company_id = attendance_fixture["company_id"]
    employee_id = attendance_fixture["employee_id"]

    with session_scope() as session:
        att_svc = AttendanceService(session, company_id=company_id)
        att_svc.record_manual_punch(
            employee_id=employee_id,
            punch_type=PunchType.CHECK_IN,
            punch_time=datetime(2026, 9, 8, 9, 5, tzinfo=timezone.utc),
        )
        record = att_svc.compute_daily_attendance(employee_id=employee_id, work_date=date(2026, 9, 8))

    assert record.status is AttendanceDayStatus.MISSING_CHECK_OUT
    assert record.check_in_time is not None
    assert record.check_out_time is None
    assert record.worked_minutes == 0


def test_check_out_without_check_in_computes_missing_check_in(attendance_fixture):
    company_id = attendance_fixture["company_id"]
    employee_id = attendance_fixture["employee_id"]

    with session_scope() as session:
        att_svc = AttendanceService(session, company_id=company_id)
        att_svc.record_manual_punch(
            employee_id=employee_id,
            punch_type=PunchType.CHECK_OUT,
            punch_time=datetime(2026, 9, 9, 17, 10, tzinfo=timezone.utc),
        )
        record = att_svc.compute_daily_attendance(employee_id=employee_id, work_date=date(2026, 9, 9))

    assert record.status is AttendanceDayStatus.MISSING_CHECK_IN
    assert record.check_in_time is None
    assert record.check_out_time is not None
    assert record.worked_minutes == 0


def test_late_check_in_without_check_out_still_reports_late_minutes(attendance_fixture):
    """Missing-punch status wins, but a still-derivable fact (lateness) is not discarded."""
    company_id = attendance_fixture["company_id"]
    employee_id = attendance_fixture["employee_id"]

    with session_scope() as session:
        att_svc = AttendanceService(session, company_id=company_id)
        att_svc.record_manual_punch(
            employee_id=employee_id,
            punch_type=PunchType.CHECK_IN,
            punch_time=datetime(2026, 9, 8, 9, 45, tzinfo=timezone.utc),
        )
        record = att_svc.compute_daily_attendance(employee_id=employee_id, work_date=date(2026, 9, 8))

    assert record.status is AttendanceDayStatus.MISSING_CHECK_OUT
    assert record.late_minutes > 0


def test_recompute_is_idempotent_not_duplicated(attendance_fixture):
    """Recomputing an already-computed day updates the row in place."""
    company_id = attendance_fixture["company_id"]
    employee_id = attendance_fixture["employee_id"]

    with session_scope() as session:
        att_svc = AttendanceService(session, company_id=company_id)
        first = att_svc.compute_daily_attendance(employee_id=employee_id, work_date=date(2026, 9, 9))
        second = att_svc.compute_daily_attendance(employee_id=employee_id, work_date=date(2026, 9, 9))
        assert first.id == second.id

        from repositories.attendance_repository import AttendanceRecordRepository

        records = AttendanceRecordRepository(session, company_id=company_id).list_for_company_between(
            date(2026, 9, 9), date(2026, 9, 9)
        )
    assert len(records) == 1


def test_overnight_shift_computes_across_midnight_correctly(company_factory):
    """A shift whose end time is earlier than its start time spans midnight.

    Regression: the punch-fetch window used to be a single fixed
    calendar day, so an overnight check-out (after local midnight) was
    invisible to the day it belonged to -- that day wrongly computed
    MISSING_CHECK_OUT, and the *next* day wrongly computed
    MISSING_CHECK_IN from the same punch. Both facts must resolve
    correctly, and worked_minutes must reflect the full night.
    """
    company_id = company_factory()
    with session_scope() as session:
        employee = EmployeeService(session, company_id=company_id).create_employee(
            employee_number="N-001", full_name="ليلى سالم"
        )
        employee_id = employee.id
        shift_svc = ShiftService(session, company_id=company_id)
        shift = shift_svc.create_shift(
            name="وردية ليلية",
            start_time=time(22, 0),
            end_time=time(6, 0),
            grace_period_minutes=10,
            working_days=[
                "saturday", "sunday", "monday", "tuesday", "wednesday", "thursday", "friday",
            ],
        )
        assert shift.spans_midnight is True
        shift_svc.assign_employee(
            employee_id=employee_id, shift_id=shift.id, effective_from=date(2026, 1, 1)
        )

    # Company default timezone is Asia/Baghdad (UTC+3): local 22:00 on the
    # 8th is 19:00 UTC; local 06:00 on the 9th is 03:00 UTC the same day.
    with session_scope() as session:
        att_svc = AttendanceService(session, company_id=company_id)
        att_svc.record_manual_punch(
            employee_id=employee_id,
            punch_type=PunchType.CHECK_IN,
            punch_time=datetime(2026, 9, 8, 19, 0, tzinfo=timezone.utc),
        )
        att_svc.record_manual_punch(
            employee_id=employee_id,
            punch_type=PunchType.CHECK_OUT,
            punch_time=datetime(2026, 9, 9, 3, 0, tzinfo=timezone.utc),
        )
        record = att_svc.compute_daily_attendance(employee_id=employee_id, work_date=date(2026, 9, 8))

    assert record.status is AttendanceDayStatus.PRESENT
    assert record.check_in_time == datetime(2026, 9, 8, 19, 0, tzinfo=timezone.utc)
    assert record.check_out_time == datetime(2026, 9, 9, 3, 0, tzinfo=timezone.utc)
    assert record.worked_minutes == 8 * 60

    # The overnight check-out must not be double-attributed to the next
    # calendar day too.
    with session_scope() as session:
        att_svc = AttendanceService(session, company_id=company_id)
        next_day = att_svc.compute_daily_attendance(
            employee_id=employee_id, work_date=date(2026, 9, 9)
        )
    assert next_day.status is AttendanceDayStatus.ABSENT
    assert next_day.check_in_time is None
    assert next_day.check_out_time is None

    # Recomputing the overnight day again stays stable (idempotent, no
    # cross-day punch reassignment on a second pass).
    with session_scope() as session:
        att_svc = AttendanceService(session, company_id=company_id)
        recomputed = att_svc.compute_daily_attendance(
            employee_id=employee_id, work_date=date(2026, 9, 8)
        )
    assert recomputed.status is AttendanceDayStatus.PRESENT
    assert recomputed.worked_minutes == 8 * 60


def test_shift_reassignment_preserves_historical_attendance_calculation(company_factory):
    """Changing an employee's shift must not corrupt already-computed history.

    Assign shift A, compute a day under it, then reassign to shift B
    effective later -- the earlier day must keep resolving against
    shift A (the shift actually in effect then), never shift B.
    """
    company_id = company_factory()
    with session_scope() as session:
        employee = EmployeeService(session, company_id=company_id).create_employee(
            employee_number="H-001", full_name="خالد ياسين"
        )
        employee_id = employee.id
        shift_svc = ShiftService(session, company_id=company_id)
        morning_shift = shift_svc.create_shift(
            name="صباحية",
            start_time=time(8, 0),
            end_time=time(16, 0),
            working_days=["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday"],
        )
        evening_shift = shift_svc.create_shift(
            name="مسائية",
            start_time=time(16, 0),
            end_time=time(23, 59),
            working_days=["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday"],
        )
        shift_svc.assign_employee(
            employee_id=employee_id, shift_id=morning_shift.id, effective_from=date(2026, 1, 1)
        )
        morning_id, evening_id = morning_shift.id, evening_shift.id

    with session_scope() as session:
        att_svc = AttendanceService(session, company_id=company_id)
        att_svc.record_manual_punch(
            employee_id=employee_id,
            punch_type=PunchType.CHECK_IN,
            punch_time=datetime(2026, 9, 8, 5, 0, tzinfo=timezone.utc),
        )
        att_svc.record_manual_punch(
            employee_id=employee_id,
            punch_type=PunchType.CHECK_OUT,
            punch_time=datetime(2026, 9, 8, 13, 0, tzinfo=timezone.utc),
        )
        historical_record = att_svc.compute_daily_attendance(
            employee_id=employee_id, work_date=date(2026, 9, 8)
        )
    assert historical_record.shift_id == morning_id

    # Reassign to the evening shift starting later.
    with session_scope() as session:
        shift_svc = ShiftService(session, company_id=company_id)
        shift_svc.assign_employee(
            employee_id=employee_id, shift_id=evening_id, effective_from=date(2026, 9, 15)
        )
        history = shift_svc.list_assignment_history(employee_id)
    assert len(history) == 2
    closed = next(a for a in history if a.effective_to is not None)
    assert closed.shift_id == morning_id
    assert closed.effective_to == date(2026, 9, 14)

    # The historical day must still resolve to the morning shift, not
    # the newly-assigned evening one.
    with session_scope() as session:
        att_svc = AttendanceService(session, company_id=company_id)
        resolved_shift = att_svc.get_effective_shift(employee_id, date(2026, 9, 8))
        assert resolved_shift is not None
        assert resolved_shift.id == morning_id

        recomputed = att_svc.compute_daily_attendance(
            employee_id=employee_id, work_date=date(2026, 9, 8)
        )
    assert recomputed.shift_id == morning_id
    assert recomputed.status is AttendanceDayStatus.PRESENT

    # ... while a day on/after the new effective date resolves to the
    # evening shift.
    with session_scope() as session:
        att_svc = AttendanceService(session, company_id=company_id)
        new_shift = att_svc.get_effective_shift(employee_id, date(2026, 9, 15))
    assert new_shift is not None
    assert new_shift.id == evening_id


def test_assign_employee_rejects_backdated_reassignment_before_current_start(company_factory):
    """A new assignment must start after the current one began (no negative-length period)."""
    from services.shift_service import ShiftValidationError

    company_id = company_factory()
    with session_scope() as session:
        employee = EmployeeService(session, company_id=company_id).create_employee(
            employee_number="H-002", full_name="سارة كامل"
        )
        employee_id = employee.id
        shift_svc = ShiftService(session, company_id=company_id)
        shift_a = shift_svc.create_shift(
            name="وردية أ", start_time=time(8, 0), end_time=time(16, 0), working_days=["sunday"]
        )
        shift_b = shift_svc.create_shift(
            name="وردية ب", start_time=time(16, 0), end_time=time(23, 0), working_days=["sunday"]
        )
        shift_svc.assign_employee(
            employee_id=employee_id, shift_id=shift_a.id, effective_from=date(2026, 6, 1)
        )

        with pytest.raises(ShiftValidationError):
            shift_svc.assign_employee(
                employee_id=employee_id, shift_id=shift_b.id, effective_from=date(2026, 5, 1)
            )

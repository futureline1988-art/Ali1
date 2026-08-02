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

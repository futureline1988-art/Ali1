"""DashboardController: executive aggregation correctness and RBAC."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from controllers.dashboard_controller import DashboardController
from database.database import session_scope
from models.enums import PunchType
from services.attendance_service import AttendanceService
from services.department_service import DepartmentService
from services.employee_service import EmployeeService
from services.shift_service import ShiftService

_BAGHDAD = ZoneInfo("Asia/Baghdad")


def _local_punch(day: date, hour: int, minute: int) -> datetime:
    """Build a UTC punch timestamp for a given Baghdad-local wall-clock time."""
    return datetime.combine(day, time(hour, minute), tzinfo=_BAGHDAD).astimezone(timezone.utc)


def test_get_summary_denied_without_permission(qapp, company_factory):
    company_id = company_factory()
    controller = DashboardController(company_id=company_id, permission_codes=frozenset())
    assert controller.get_summary() is None


def test_trend_and_breakdown_denied_return_empty_list(qapp, company_factory):
    company_id = company_factory()
    controller = DashboardController(company_id=company_id, permission_codes=frozenset())
    assert controller.get_attendance_trend() == []
    assert controller.get_department_breakdown() == []


def test_department_breakdown_groups_correctly(qapp, company_factory):
    company_id = company_factory()
    with session_scope() as session:
        dept_a = DepartmentService(session, company_id=company_id).create_department(name="أول")
        dept_b = DepartmentService(session, company_id=company_id).create_department(name="ثاني")
        emp_svc = EmployeeService(session, company_id=company_id)
        emp_svc.create_employee(employee_number="D1", full_name="واحد", department_id=dept_a.id)
        emp_svc.create_employee(employee_number="D2", full_name="اثنان", department_id=dept_a.id)
        emp_svc.create_employee(employee_number="D3", full_name="ثلاثة", department_id=dept_b.id)
        emp_svc.create_employee(employee_number="D4", full_name="أربعة")  # no department

    controller = DashboardController(
        company_id=company_id, permission_codes=frozenset({"dashboard.view"})
    )
    breakdown = controller.get_department_breakdown()
    by_name = {row["name"]: row["employee_count"] for row in breakdown}

    assert by_name["أول"] == 2
    assert by_name["ثاني"] == 1
    assert by_name["غير محدد"] == 1


def test_attendance_trend_buckets_today_by_status(qapp, company_factory):
    company_id = company_factory()
    today = datetime.now(_BAGHDAD).date()

    with session_scope() as session:
        emp_svc = EmployeeService(session, company_id=company_id)
        present_employee = emp_svc.create_employee(employee_number="T1", full_name="حاضر")
        absent_employee = emp_svc.create_employee(employee_number="T2", full_name="غائب")

        shift_svc = ShiftService(session, company_id=company_id)
        shift = shift_svc.create_shift(
            name="وردية",
            start_time=time(9, 0),
            end_time=time(17, 0),
            grace_period_minutes=10,
            working_days=[
                "saturday", "sunday", "monday", "tuesday",
                "wednesday", "thursday", "friday",
            ],
        )
        for employee in (present_employee, absent_employee):
            shift_svc.assign_employee(
                employee_id=employee.id, shift_id=shift.id, effective_from=today - timedelta(days=1)
            )
        present_id, absent_id = present_employee.id, absent_employee.id

    with session_scope() as session:
        att_svc = AttendanceService(session, company_id=company_id)
        att_svc.record_manual_punch(
            employee_id=present_id, punch_type=PunchType.CHECK_IN,
            punch_time=_local_punch(today, 9, 0),
        )
        att_svc.record_manual_punch(
            employee_id=present_id, punch_type=PunchType.CHECK_OUT,
            punch_time=_local_punch(today, 17, 0),
        )
        att_svc.compute_daily_attendance(employee_id=present_id, work_date=today)
        att_svc.compute_daily_attendance(employee_id=absent_id, work_date=today)

    controller = DashboardController(
        company_id=company_id, permission_codes=frozenset({"dashboard.view"})
    )
    trend = controller.get_attendance_trend(days=7)
    assert len(trend) == 7
    todays_row = trend[-1]
    assert todays_row["date"] == today.isoformat()
    assert todays_row["present"] == 1
    assert todays_row["absent"] == 1

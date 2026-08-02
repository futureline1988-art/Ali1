"""Dashboard endpoints — the HTTP mirror of ``controllers/dashboard_controller.py``.

Reimplements (rather than imports) that controller's three aggregation
queries: :class:`~controllers.dashboard_controller.DashboardController`
extends ``PySide6.QtCore.QObject``, and pulling Qt into this otherwise
Qt-free API process just to reuse three query bodies is not worth
coupling a headless HTTP server to the desktop UI toolkit's shared
libraries. Keep the two in sync if the aggregation logic changes.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.dependencies import CurrentUser, get_db_session, require_permission
from models.enums import AttendanceDayStatus, DeviceStatus
from repositories.attendance_repository import AttendanceRecordRepository
from repositories.company_settings_repository import CompanySettingsRepository
from repositories.department_repository import DepartmentRepository
from repositories.device_repository import DeviceRepository
from repositories.employee_repository import EmployeeRepository

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_TREND_DAYS = 14


def _company_today(session: Session, *, company_id: int) -> date:
    """Resolve "today" in this company's configured local timezone."""
    settings = CompanySettingsRepository(session, company_id=company_id).get_for_company()
    tz_name = settings.timezone_name if settings is not None else "Asia/Baghdad"
    return datetime.now(ZoneInfo(tz_name)).date()


@router.get("/summary")
def get_summary(
    current_user: CurrentUser = Depends(require_permission("dashboard.view")),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Build the dashboard's full summary snapshot for the caller's company."""
    company_id = current_user.company_id
    today = _company_today(session, company_id=company_id)

    employee_repo = EmployeeRepository(session, company_id=company_id)
    active_employees = employee_repo.list_active()
    active_employee_count = len(active_employees)

    department_count = DepartmentRepository(session, company_id=company_id).count()

    device_repo = DeviceRepository(session, company_id=company_id)
    devices = device_repo.list_all()
    device_status_counts = {status: 0 for status in DeviceStatus}
    for device in devices:
        device_status_counts[device.status] += 1

    attendance_repo = AttendanceRecordRepository(session, company_id=company_id)
    today_records = attendance_repo.list_for_company_between(today, today)
    active_employee_ids = {employee.id for employee in active_employees}
    records_by_employee = {
        record.employee_id: record
        for record in today_records
        if record.employee_id in active_employee_ids
    }
    attendance_today = {status.value: 0 for status in AttendanceDayStatus}
    for record in records_by_employee.values():
        attendance_today[record.status.value] += 1
    attendance_today["not_yet_computed"] = active_employee_count - len(records_by_employee)

    return {
        "today": today.isoformat(),
        "active_employee_count": active_employee_count,
        "total_employee_count": employee_repo.count(),
        "department_count": department_count,
        "device_count": len(devices),
        "devices_online": device_status_counts[DeviceStatus.ONLINE],
        "devices_offline": device_status_counts[DeviceStatus.OFFLINE],
        "devices_error": device_status_counts[DeviceStatus.ERROR],
        "devices_unknown": device_status_counts[DeviceStatus.UNKNOWN],
        "attendance_today": attendance_today,
    }


@router.get("/trend")
def get_attendance_trend(
    days: int = _TREND_DAYS,
    current_user: CurrentUser = Depends(require_permission("dashboard.view")),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Build the last ``days`` days of company-wide attendance status counts."""
    company_id = current_user.company_id
    today = _company_today(session, company_id=company_id)
    start = today - timedelta(days=days - 1)

    records = AttendanceRecordRepository(session, company_id=company_id).list_for_company_between(
        start, today
    )
    by_day: dict[date, dict[str, int]] = {
        start + timedelta(days=offset): {status.value: 0 for status in AttendanceDayStatus}
        for offset in range(days)
    }
    for record in records:
        counts = by_day.get(record.work_date)
        if counts is not None:
            counts[record.status.value] += 1

    return [{"date": day.isoformat(), **counts} for day, counts in sorted(by_day.items())]


@router.get("/departments")
def get_department_breakdown(
    current_user: CurrentUser = Depends(require_permission("dashboard.view")),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Build active headcount per department for the caller's company."""
    employee_repo = EmployeeRepository(session, company_id=current_user.company_id)
    counts: dict[str, int] = {}
    for employee in employee_repo.list_active():
        name = employee.department.name if employee.department else "غير محدد"
        counts[name] = counts.get(name, 0) + 1
    return sorted(
        ({"name": name, "employee_count": count} for name, count in counts.items()),
        key=lambda row: row["employee_count"],
        reverse=True,
    )

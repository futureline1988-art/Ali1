"""Dashboard controller: aggregates cross-module stats for the home screen."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from controllers.base_controller import BaseController, requires_permission
from models.enums import AttendanceDayStatus, DeviceStatus
from repositories.attendance_repository import AttendanceRecordRepository
from repositories.company_settings_repository import CompanySettingsRepository
from repositories.department_repository import DepartmentRepository
from repositories.device_repository import DeviceRepository
from repositories.employee_repository import EmployeeRepository


class DashboardController(BaseController):
    """Controller for the dashboard/home screen's summary statistics.

    Every figure is a point-in-time snapshot computed fresh on each
    call (no caching), matching how a dashboard is expected to reflect
    the database's current state the moment it is opened or refreshed.
    """

    def _company_today(self, session: Session) -> date:
        """Resolve "today" in this company's configured local timezone.

        Falls back to ``"Asia/Baghdad"`` if
        :class:`~models.company_settings.CompanySettings` has not been
        seeded for this company yet, mirroring
        :meth:`~services.attendance_service.AttendanceService._get_company_timezone`.
        """
        settings_repo = CompanySettingsRepository(session, company_id=self.company_id)
        settings = settings_repo.get_for_company()
        tz_name = settings.timezone_name if settings is not None else "Asia/Baghdad"
        return datetime.now(ZoneInfo(tz_name)).date()

    @requires_permission("dashboard.view")
    def get_summary(self) -> dict[str, Any] | None:
        """Build the dashboard's full summary snapshot.

        Returns:
            A dict with the following keys, or ``None`` on failure:

            - ``today``: today's date (company-local), ISO format.
            - ``active_employee_count`` / ``total_employee_count``
            - ``department_count``
            - ``device_count`` / ``devices_online`` / ``devices_offline``
              / ``devices_error`` / ``devices_unknown``
            - ``attendance_today``: a dict keyed by each
              :class:`~models.enums.AttendanceDayStatus` value plus
              ``not_yet_computed``, counting today's active employees.
        """

        def do_summary(session: Session) -> dict[str, Any]:
            today = self._company_today(session)

            employee_repo = EmployeeRepository(session, company_id=self.company_id)
            active_employees = employee_repo.list_active()
            active_employee_count = len(active_employees)
            total_employee_count = employee_repo.count()

            department_repo = DepartmentRepository(session, company_id=self.company_id)
            department_count = department_repo.count()

            device_repo = DeviceRepository(session, company_id=self.company_id)
            devices = device_repo.list_all()
            device_status_counts = {status: 0 for status in DeviceStatus}
            for device in devices:
                device_status_counts[device.status] += 1

            attendance_repo = AttendanceRecordRepository(session, company_id=self.company_id)
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
            attendance_today["not_yet_computed"] = (
                active_employee_count - len(records_by_employee)
            )

            return {
                "today": today.isoformat(),
                "active_employee_count": active_employee_count,
                "total_employee_count": total_employee_count,
                "department_count": department_count,
                "device_count": len(devices),
                "devices_online": device_status_counts[DeviceStatus.ONLINE],
                "devices_offline": device_status_counts[DeviceStatus.OFFLINE],
                "devices_error": device_status_counts[DeviceStatus.ERROR],
                "devices_unknown": device_status_counts[DeviceStatus.UNKNOWN],
                "attendance_today": attendance_today,
            }

        return self._run(do_summary)

"""Report controller: bridges the reports screen to ``ReportService``."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import Signal
from sqlalchemy.orm import Session

from controllers.base_controller import BaseController, requires_permission
from models.enums import ReportFormat, ReportType
from services.report_service import (
    ABSENCE_COLUMNS,
    ATTENDANCE_COLUMNS,
    LATE_COLUMNS,
    OVERTIME_COLUMNS,
    PAYROLL_ADJUSTMENT_COLUMNS,
    PAYROLL_SUMMARY_COLUMNS,
    ReportService,
)

_TITLES_AR: dict[ReportType, str] = {
    ReportType.ATTENDANCE_SUMMARY: "ملخص الحضور",
    ReportType.BY_EMPLOYEE: "تقرير الموظف",
    ReportType.BY_DEPARTMENT: "تقرير القسم",
    ReportType.LATE_EMPLOYEES: "الموظفون المتأخرون",
    ReportType.OVERTIME: "الوقت الإضافي",
    ReportType.ABSENCE: "تقرير الغياب",
    ReportType.PAYROLL_SUMMARY: "ملخص الرواتب",
    ReportType.PAYROLL_DEDUCTIONS: "الخصومات",
    ReportType.PAYROLL_BONUSES: "المكافآت والإضافات",
}


class ReportController(BaseController):
    """Controller for generating and exporting attendance reports."""

    report_generated = Signal(str)
    """Emitted with the output file path after a successful export."""

    @requires_permission("reports.view", "reports.export")
    def generate_report(
        self,
        *,
        report_type: ReportType,
        output_format: ReportFormat,
        output_path: Path,
        start_date: date | None = None,
        end_date: date | None = None,
        employee_id: int | None = None,
        department_id: int | None = None,
        year: int | None = None,
        month: int | None = None,
    ) -> Path | None:
        """Build and export a report.

        Args:
            report_type: Which report to build (attendance summary,
                by-employee, by-department, late employees, overtime,
                absence, or one of the payroll report types).
            output_format: Which file format to produce.
            output_path: Destination path.
            start_date: First day to include (inclusive); required for
                every attendance-based report type.
            end_date: Last day to include (inclusive); required for
                every attendance-based report type.
            employee_id: Restrict to one employee (only meaningful for
                :attr:`~models.enums.ReportType.ATTENDANCE_SUMMARY` /
                :attr:`~models.enums.ReportType.BY_EMPLOYEE`).
            department_id: Restrict to one department (only meaningful
                for the same two report types).
            year: The pay period's calendar year; required for
                :attr:`~models.enums.ReportType.PAYROLL_SUMMARY`/
                :attr:`~models.enums.ReportType.PAYROLL_DEDUCTIONS`/
                :attr:`~models.enums.ReportType.PAYROLL_BONUSES`.
            month: The pay period's calendar month (1-12); required
                for the same three payroll report types.

        Returns:
            The output file path on success, or ``None`` on failure.

        Raises:
            ValueError: If ``report_type`` is not recognized, or a
                required argument for it is missing (raised inside the
                controller boundary, so it surfaces via
                :attr:`~controllers.base_controller.BaseController.operation_failed`,
                not as a raw exception).
        """

        def do_generate(session: Session) -> Path:
            service = ReportService(session, company_id=self.company_id)

            if report_type in (ReportType.ATTENDANCE_SUMMARY, ReportType.BY_EMPLOYEE):
                if start_date is None or end_date is None:
                    raise ValueError("This report type requires a start_date and end_date.")
                rows = service.build_attendance_rows(
                    start_date=start_date,
                    end_date=end_date,
                    employee_id=employee_id,
                    department_id=department_id,
                )
                columns = ATTENDANCE_COLUMNS
            elif report_type is ReportType.BY_DEPARTMENT:
                if start_date is None or end_date is None:
                    raise ValueError("This report type requires a start_date and end_date.")
                rows = service.build_attendance_rows(
                    start_date=start_date, end_date=end_date, department_id=department_id
                )
                columns = ATTENDANCE_COLUMNS
            elif report_type is ReportType.LATE_EMPLOYEES:
                if start_date is None or end_date is None:
                    raise ValueError("This report type requires a start_date and end_date.")
                rows = service.build_late_employees_rows(
                    start_date=start_date, end_date=end_date
                )
                columns = LATE_COLUMNS
            elif report_type is ReportType.OVERTIME:
                if start_date is None or end_date is None:
                    raise ValueError("This report type requires a start_date and end_date.")
                rows = service.build_overtime_rows(start_date=start_date, end_date=end_date)
                columns = OVERTIME_COLUMNS
            elif report_type is ReportType.ABSENCE:
                if start_date is None or end_date is None:
                    raise ValueError("This report type requires a start_date and end_date.")
                rows = service.build_absence_rows(start_date=start_date, end_date=end_date)
                columns = ABSENCE_COLUMNS
            elif report_type is ReportType.PAYROLL_SUMMARY:
                if year is None or month is None:
                    raise ValueError("This report type requires a year and month.")
                rows = service.build_payroll_summary_rows(year=year, month=month)
                columns = PAYROLL_SUMMARY_COLUMNS
            elif report_type is ReportType.PAYROLL_DEDUCTIONS:
                if year is None or month is None:
                    raise ValueError("This report type requires a year and month.")
                rows = service.build_payroll_deductions_rows(year=year, month=month)
                columns = PAYROLL_ADJUSTMENT_COLUMNS
            elif report_type is ReportType.PAYROLL_BONUSES:
                if year is None or month is None:
                    raise ValueError("This report type requires a year and month.")
                rows = service.build_payroll_bonuses_rows(year=year, month=month)
                columns = PAYROLL_ADJUSTMENT_COLUMNS
            else:
                raise ValueError(f"Unsupported report type: {report_type!r}")

            title = _TITLES_AR.get(report_type, "تقرير")
            return service.export(
                rows,
                columns,
                output_format=output_format,
                output_path=output_path,
                title=title,
            )

        result = self._run(do_generate)
        if result is not None:
            self.report_generated.emit(str(result))
        return result

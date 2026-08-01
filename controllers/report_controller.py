"""Report controller: bridges the reports screen to ``ReportService``."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import Signal
from sqlalchemy.orm import Session

from controllers.base_controller import BaseController
from models.enums import ReportFormat, ReportType
from services.report_service import (
    ABSENCE_COLUMNS,
    ATTENDANCE_COLUMNS,
    LATE_COLUMNS,
    OVERTIME_COLUMNS,
    ReportService,
)

_TITLES_AR: dict[ReportType, str] = {
    ReportType.ATTENDANCE_SUMMARY: "ملخص الحضور",
    ReportType.BY_EMPLOYEE: "تقرير الموظف",
    ReportType.BY_DEPARTMENT: "تقرير القسم",
    ReportType.LATE_EMPLOYEES: "الموظفون المتأخرون",
    ReportType.OVERTIME: "الوقت الإضافي",
    ReportType.ABSENCE: "تقرير الغياب",
}


class ReportController(BaseController):
    """Controller for generating and exporting attendance reports."""

    report_generated = Signal(str)
    """Emitted with the output file path after a successful export."""

    def generate_report(
        self,
        *,
        report_type: ReportType,
        output_format: ReportFormat,
        output_path: Path,
        start_date: date,
        end_date: date,
        employee_id: int | None = None,
        department_id: int | None = None,
    ) -> Path | None:
        """Build and export a report.

        Args:
            report_type: Which report to build (attendance summary,
                by-employee, by-department, late employees, overtime,
                or absence).
            output_format: Which file format to produce.
            output_path: Destination path.
            start_date: First day to include (inclusive).
            end_date: Last day to include (inclusive).
            employee_id: Restrict to one employee (only meaningful for
                :attr:`~models.enums.ReportType.ATTENDANCE_SUMMARY` /
                :attr:`~models.enums.ReportType.BY_EMPLOYEE`).
            department_id: Restrict to one department (only meaningful
                for the same two report types).

        Returns:
            The output file path on success, or ``None`` on failure.

        Raises:
            ValueError: If ``report_type`` is not recognized (raised
                inside the controller boundary, so it surfaces via
                :attr:`~controllers.base_controller.BaseController.operation_failed`,
                not as a raw exception).
        """

        def do_generate(session: Session) -> Path:
            service = ReportService(session, company_id=self.company_id)

            if report_type in (ReportType.ATTENDANCE_SUMMARY, ReportType.BY_EMPLOYEE):
                rows = service.build_attendance_rows(
                    start_date=start_date,
                    end_date=end_date,
                    employee_id=employee_id,
                    department_id=department_id,
                )
                columns = ATTENDANCE_COLUMNS
            elif report_type is ReportType.BY_DEPARTMENT:
                rows = service.build_attendance_rows(
                    start_date=start_date, end_date=end_date, department_id=department_id
                )
                columns = ATTENDANCE_COLUMNS
            elif report_type is ReportType.LATE_EMPLOYEES:
                rows = service.build_late_employees_rows(
                    start_date=start_date, end_date=end_date
                )
                columns = LATE_COLUMNS
            elif report_type is ReportType.OVERTIME:
                rows = service.build_overtime_rows(start_date=start_date, end_date=end_date)
                columns = OVERTIME_COLUMNS
            elif report_type is ReportType.ABSENCE:
                rows = service.build_absence_rows(start_date=start_date, end_date=end_date)
                columns = ABSENCE_COLUMNS
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

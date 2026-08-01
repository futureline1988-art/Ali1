"""Report generation service: builds report rows and dispatches to an exporter.

Bridges the repository layer's ORM objects to the plain
``(rows, columns)`` shape every exporter in ``utils/`` shares, and
covers the report categories the product specifies: daily/weekly/
monthly/annual attendance (any date range, really — the caller decides
the granularity by choosing ``start_date``/``end_date``), by employee,
by department, late employees, and overtime/absence.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from models.attendance import AttendanceRecord
from models.enums import AttendanceDayStatus, ReportFormat
from repositories.attendance_repository import AttendanceRecordRepository
from repositories.department_repository import DepartmentRepository
from repositories.employee_repository import EmployeeRepository
from utils.csv_export import export_to_csv
from utils.excel import export_to_excel
from utils.i18n import format_date, format_time
from utils.pdf import export_to_pdf

ATTENDANCE_COLUMNS: list[tuple[str, str]] = [
    ("employee_number", "رقم الموظف"),
    ("full_name", "الاسم"),
    ("department", "القسم"),
    ("work_date", "التاريخ"),
    ("status", "الحالة"),
    ("check_in", "الحضور"),
    ("check_out", "الانصراف"),
    ("late_minutes", "دقائق التأخير"),
    ("overtime_minutes", "دقائق الإضافي"),
    ("worked_hours", "ساعات العمل"),
]

LATE_COLUMNS: list[tuple[str, str]] = [
    ("employee_number", "رقم الموظف"),
    ("full_name", "الاسم"),
    ("work_date", "التاريخ"),
    ("late_minutes", "دقائق التأخير"),
]

OVERTIME_COLUMNS: list[tuple[str, str]] = [
    ("employee_number", "رقم الموظف"),
    ("full_name", "الاسم"),
    ("work_date", "التاريخ"),
    ("overtime_minutes", "دقائق الإضافي"),
]

ABSENCE_COLUMNS: list[tuple[str, str]] = [
    ("employee_number", "رقم الموظف"),
    ("full_name", "الاسم"),
    ("department", "القسم"),
    ("work_date", "التاريخ"),
]


class ReportService:
    """Report generation scoped to one company.

    Attributes:
        session: The active database session.
        company_id: The company this service operates within.
    """

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create a report service bound to one session and company.

        Args:
            session: The active database session.
            company_id: The company to report on.
        """
        self.session = session
        self.company_id = company_id
        self.record_repo = AttendanceRecordRepository(session, company_id=company_id)
        self.employee_repo = EmployeeRepository(session, company_id=company_id)
        self.department_repo = DepartmentRepository(session, company_id=company_id)

    def _fetch_records(
        self,
        *,
        start_date: date,
        end_date: date,
        employee_id: int | None = None,
        department_id: int | None = None,
    ) -> list[AttendanceRecord]:
        """Fetch attendance records for a range, optionally filtered."""
        if employee_id is not None:
            return self.record_repo.list_for_employee_between(
                employee_id, start_date, end_date
            )
        records = self.record_repo.list_for_company_between(start_date, end_date)
        if department_id is not None:
            records = [
                record
                for record in records
                if record.employee.department_id == department_id
            ]
        return records

    @staticmethod
    def _record_to_row(record: AttendanceRecord) -> dict[str, object]:
        """Convert one attendance record into a report row dict."""
        employee = record.employee
        return {
            "employee_number": employee.employee_number,
            "full_name": employee.full_name,
            "department": employee.department.name if employee.department else "",
            "work_date": format_date(record.work_date),
            "status": record.status_label_ar,
            "check_in": format_time(record.check_in_time.time())
            if record.check_in_time
            else "",
            "check_out": format_time(record.check_out_time.time())
            if record.check_out_time
            else "",
            "late_minutes": record.late_minutes,
            "overtime_minutes": record.overtime_minutes,
            "worked_hours": record.worked_hours,
        }

    def build_attendance_rows(
        self,
        *,
        start_date: date,
        end_date: date,
        employee_id: int | None = None,
        department_id: int | None = None,
    ) -> list[dict[str, object]]:
        """Build the full attendance report's rows for a date range.

        Args:
            start_date: First day to include (inclusive).
            end_date: Last day to include (inclusive).
            employee_id: Restrict to one employee, if given.
            department_id: Restrict to one department, if given
                (ignored when ``employee_id`` is also given).

        Returns:
            One row per attendance record, sorted as returned by the
            repository (chronological).
        """
        records = self._fetch_records(
            start_date=start_date,
            end_date=end_date,
            employee_id=employee_id,
            department_id=department_id,
        )
        return [self._record_to_row(record) for record in records]

    def build_late_employees_rows(
        self, *, start_date: date, end_date: date
    ) -> list[dict[str, object]]:
        """Build rows for every late-arrival record in a date range.

        Args:
            start_date: First day to include (inclusive).
            end_date: Last day to include (inclusive).

        Returns:
            One row per record where
            :attr:`~models.attendance.AttendanceRecord.is_late` is
            true.
        """
        records = self._fetch_records(start_date=start_date, end_date=end_date)
        return [
            {
                "employee_number": r.employee.employee_number,
                "full_name": r.employee.full_name,
                "work_date": format_date(r.work_date),
                "late_minutes": r.late_minutes,
            }
            for r in records
            if r.is_late
        ]

    def build_overtime_rows(
        self, *, start_date: date, end_date: date
    ) -> list[dict[str, object]]:
        """Build rows for every record with recorded overtime in a date range.

        Args:
            start_date: First day to include (inclusive).
            end_date: Last day to include (inclusive).

        Returns:
            One row per record where
            :attr:`~models.attendance.AttendanceRecord.has_overtime` is
            true.
        """
        records = self._fetch_records(start_date=start_date, end_date=end_date)
        return [
            {
                "employee_number": r.employee.employee_number,
                "full_name": r.employee.full_name,
                "work_date": format_date(r.work_date),
                "overtime_minutes": r.overtime_minutes,
            }
            for r in records
            if r.has_overtime
        ]

    def build_absence_rows(
        self, *, start_date: date, end_date: date
    ) -> list[dict[str, object]]:
        """Build rows for every absence record in a date range.

        Args:
            start_date: First day to include (inclusive).
            end_date: Last day to include (inclusive).

        Returns:
            One row per record with
            :attr:`~models.enums.AttendanceDayStatus.ABSENT` status.
        """
        records = self._fetch_records(start_date=start_date, end_date=end_date)
        return [
            {
                "employee_number": r.employee.employee_number,
                "full_name": r.employee.full_name,
                "department": r.employee.department.name if r.employee.department else "",
                "work_date": format_date(r.work_date),
            }
            for r in records
            if r.status is AttendanceDayStatus.ABSENT
        ]

    def export(
        self,
        rows: list[dict[str, object]],
        columns: list[tuple[str, str]],
        *,
        output_format: ReportFormat,
        output_path: Path,
        title: str = "تقرير",
    ) -> Path:
        """Dispatch ``rows``/``columns`` to the requested exporter.

        Args:
            rows: Row data, as produced by one of the ``build_*``
                methods.
            columns: The matching column spec (e.g.
                :data:`ATTENDANCE_COLUMNS`).
            output_format: Which file format to produce.
            output_path: Destination path.
            title: Report title, used by the PDF exporter (ignored by
                Excel/CSV).

        Returns:
            ``output_path``, for chaining.

        Raises:
            ValueError: If ``output_format`` is not a recognized
                :class:`~models.enums.ReportFormat`.
        """
        if output_format is ReportFormat.EXCEL:
            return export_to_excel(rows, columns, output_path)
        if output_format is ReportFormat.PDF:
            return export_to_pdf(rows, columns, output_path, title=title)
        if output_format is ReportFormat.CSV:
            return export_to_csv(rows, columns, output_path)
        raise ValueError(f"Unsupported report format: {output_format!r}")

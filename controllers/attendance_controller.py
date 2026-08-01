"""Attendance controller: bridges the attendance screen to ``AttendanceService``."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from PySide6.QtCore import Signal
from sqlalchemy.orm import Session

from controllers.base_controller import BaseController
from models.attendance import AttendanceRecord
from models.enums import PunchType
from services.attendance_service import AttendanceService


def _record_to_dict(record: AttendanceRecord) -> dict[str, Any]:
    """Serialize an attendance record plus its employee's name, while the session is open."""
    data = record.to_dict()
    data["employee_number"] = record.employee.employee_number
    data["employee_full_name"] = record.employee.full_name
    data["status_label_ar"] = record.status_label_ar
    data["status_label_en"] = record.status_label_en
    data["worked_hours"] = record.worked_hours
    data["overtime_hours"] = record.overtime_hours
    return data


class AttendanceController(BaseController):
    """Controller for manual attendance entry and daily computation."""

    attendance_changed = Signal()
    """Emitted after a manual punch is recorded or a day is (re)computed."""

    def record_manual_punch(
        self,
        *,
        employee_id: int,
        punch_type: PunchType,
        punch_time: datetime | None = None,
        notes: str | None = None,
    ) -> bool:
        """Record a manually-entered punch.

        Args:
            employee_id: The employee punching in/out.
            punch_type: Check-in or check-out.
            punch_time: When the punch occurred; defaults to now.
            notes: Optional reason for the manual entry.

        Returns:
            ``True`` on success, ``False`` on failure.
        """

        def do_record(session: Session) -> bool:
            service = AttendanceService(session, company_id=self.company_id)
            service.record_manual_punch(
                employee_id=employee_id,
                punch_type=punch_type,
                punch_time=punch_time,
                notes=notes,
            )
            return True

        result = self._run(do_record)
        if result:
            self.attendance_changed.emit()
        return bool(result)

    def compute_daily_attendance(
        self, *, employee_id: int, work_date: date
    ) -> dict[str, Any] | None:
        """Compute (or recompute) one employee's attendance for one day.

        Args:
            employee_id: The employee to compute.
            work_date: The day to compute.

        Returns:
            The computed record's data as a dict, or ``None`` on
            failure.
        """

        def do_compute(session: Session) -> dict[str, Any]:
            service = AttendanceService(session, company_id=self.company_id)
            record = service.compute_daily_attendance(
                employee_id=employee_id, work_date=work_date
            )
            return _record_to_dict(record)

        result = self._run(do_compute)
        if result is not None:
            self.attendance_changed.emit()
        return result

    def bulk_compute_for_date(self, work_date: date) -> list[dict[str, Any]]:
        """Compute attendance for every active employee on one day.

        Args:
            work_date: The day to compute.

        Returns:
            The computed records' data as dicts; an empty list on
            failure.
        """

        def do_bulk(session: Session) -> list[dict[str, Any]]:
            service = AttendanceService(session, company_id=self.company_id)
            records = service.bulk_compute_for_date(work_date)
            return [_record_to_dict(record) for record in records]

        result = self._run(do_bulk)
        if result:
            self.attendance_changed.emit()
        return result or []

    def list_for_employee_between(
        self, employee_id: int, start_date: date, end_date: date
    ) -> list[dict[str, Any]]:
        """List one employee's attendance records within a date range.

        Args:
            employee_id: The employee to query.
            start_date: First day (inclusive).
            end_date: Last day (inclusive).

        Returns:
            Matching records' data as dicts; an empty list on failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            from repositories.attendance_repository import AttendanceRecordRepository

            repo = AttendanceRecordRepository(session, company_id=self.company_id)
            records = repo.list_for_employee_between(employee_id, start_date, end_date)
            return [_record_to_dict(record) for record in records]

        return self._run(do_list) or []

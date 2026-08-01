"""Repositories for :class:`~models.attendance.AttendanceRecord` and
:class:`~models.attendance.AttendancePunch`.

Grouped in one file, mirroring how the two models are grouped in
``models/attendance.py``.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.attendance import AttendancePunch, AttendanceRecord
from repositories.base_repository import CompanyScopedRepository


class AttendanceRecordRepository(CompanyScopedRepository[AttendanceRecord]):
    """Data access for :class:`~models.attendance.AttendanceRecord`."""

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create a repository bound to ``session`` and ``company_id``."""
        super().__init__(session, model=AttendanceRecord, company_id=company_id)

    def get_for_employee_and_date(
        self, employee_id: int, work_date: date
    ) -> AttendanceRecord | None:
        """Fetch one employee's attendance record for one specific day.

        Args:
            employee_id: The employee's id.
            work_date: The calendar day to fetch.

        Returns:
            The matching record, or ``None`` if none exists yet for
            that day.
        """
        statement = select(AttendanceRecord).where(
            AttendanceRecord.company_id == self.company_id,
            AttendanceRecord.employee_id == employee_id,
            AttendanceRecord.work_date == work_date,
            AttendanceRecord.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

    def list_for_employee_between(
        self, employee_id: int, start_date: date, end_date: date
    ) -> list[AttendanceRecord]:
        """List one employee's attendance records within a date range.

        Args:
            employee_id: The employee's id.
            start_date: First day to include (inclusive).
            end_date: Last day to include (inclusive).

        Returns:
            Matching records, ordered chronologically.
        """
        statement = (
            select(AttendanceRecord)
            .where(
                AttendanceRecord.company_id == self.company_id,
                AttendanceRecord.employee_id == employee_id,
                AttendanceRecord.work_date >= start_date,
                AttendanceRecord.work_date <= end_date,
                AttendanceRecord.is_deleted.is_(False),
            )
            .order_by(AttendanceRecord.work_date)
        )
        return list(self.session.execute(statement).scalars().all())

    def list_for_company_between(
        self, start_date: date, end_date: date
    ) -> list[AttendanceRecord]:
        """List every employee's attendance records within a date range.

        Args:
            start_date: First day to include (inclusive).
            end_date: Last day to include (inclusive).

        Returns:
            Matching records for the whole company, ordered
            chronologically.
        """
        statement = (
            select(AttendanceRecord)
            .where(
                AttendanceRecord.company_id == self.company_id,
                AttendanceRecord.work_date >= start_date,
                AttendanceRecord.work_date <= end_date,
                AttendanceRecord.is_deleted.is_(False),
            )
            .order_by(AttendanceRecord.work_date)
        )
        return list(self.session.execute(statement).scalars().all())


class AttendancePunchRepository(CompanyScopedRepository[AttendancePunch]):
    """Data access for :class:`~models.attendance.AttendancePunch`."""

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create a repository bound to ``session`` and ``company_id``."""
        super().__init__(session, model=AttendancePunch, company_id=company_id)

    def list_unprocessed(self) -> list[AttendancePunch]:
        """List punches not yet aggregated into an
        :class:`~models.attendance.AttendanceRecord`.

        Returns:
            Punches with
            :attr:`~models.attendance.AttendancePunch.attendance_record_id`
            unset, ordered chronologically — the queue the attendance
            aggregation job works through.
        """
        statement = (
            select(AttendancePunch)
            .where(
                AttendancePunch.company_id == self.company_id,
                AttendancePunch.attendance_record_id.is_(None),
                AttendancePunch.is_deleted.is_(False),
            )
            .order_by(AttendancePunch.punch_time)
        )
        return list(self.session.execute(statement).scalars().all())

    def list_for_employee_between(
        self, employee_id: int, start: datetime, end: datetime
    ) -> list[AttendancePunch]:
        """List one employee's raw punches within a time range.

        Args:
            employee_id: The employee's id.
            start: Range start (inclusive), UTC.
            end: Range end (inclusive), UTC.

        Returns:
            Matching punches, ordered chronologically.
        """
        statement = (
            select(AttendancePunch)
            .where(
                AttendancePunch.company_id == self.company_id,
                AttendancePunch.employee_id == employee_id,
                AttendancePunch.punch_time >= start,
                AttendancePunch.punch_time <= end,
                AttendancePunch.is_deleted.is_(False),
            )
            .order_by(AttendancePunch.punch_time)
        )
        return list(self.session.execute(statement).scalars().all())

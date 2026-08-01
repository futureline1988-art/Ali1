"""Shift definition and employee-shift assignment ORM models.

A :class:`Shift` describes *when* a work period runs (start/end clock
time, grace periods, overtime threshold, active weekdays).
:class:`EmployeeShiftAssignment` records *which employee* was on *which
shift* *during which date range* — a history table rather than a single
``shift_id`` column on :class:`~models.employee.Employee`, so that
changing an employee's shift never loses the record of what shift they
were actually on for attendance already computed in the past.
"""

from __future__ import annotations

from datetime import date, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    JSON,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import BaseModel, CompanyScopedMixin
from models.enums import Weekday


class Shift(CompanyScopedMixin, BaseModel):
    """A reusable work-shift definition belonging to one company.

    Attributes:
        name: Shift name (e.g. ``"الوردية الصباحية"``); unique within the
            owning company.
        start_time: Clock time the shift begins.
        end_time: Clock time the shift ends. May be earlier than
            :attr:`start_time` for a shift that spans midnight (see
            :attr:`spans_midnight`).
        grace_period_minutes: Minutes an employee may check in after
            :attr:`start_time` before being marked late.
        early_leave_grace_minutes: Minutes an employee may check out
            before :attr:`end_time` without being marked as leaving
            early.
        overtime_threshold_minutes: Minutes an employee must stay past
            :attr:`end_time` before the extra time counts as overtime.
        working_days: :class:`~models.enums.Weekday` values (stored as
            their string codes) this shift is active on. Wrapped in
            :class:`~sqlalchemy.ext.mutable.MutableList` so in-place
            mutation is tracked.
        is_active: Whether this shift can currently be assigned to
            employees.
        description: Optional free-form notes about the shift.
    """

    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_shifts_company_id_name"),
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)

    grace_period_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    early_leave_grace_minutes: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    overtime_threshold_minutes: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )

    working_days: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON), default=list, nullable=False
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    @property
    def spans_midnight(self) -> bool:
        """Whether this shift crosses midnight (ends at/before it starts)."""
        return self.end_time <= self.start_time

    @property
    def duration_minutes(self) -> int:
        """Total shift length in minutes, correctly handling midnight-spanning shifts."""
        start_minutes = self.start_time.hour * 60 + self.start_time.minute
        end_minutes = self.end_time.hour * 60 + self.end_time.minute
        if end_minutes <= start_minutes:
            end_minutes += 24 * 60
        return end_minutes - start_minutes

    def is_working_day(self, weekday: Weekday) -> bool:
        """Whether this shift is active on the given day of the week.

        Args:
            weekday: The day to check.

        Returns:
            ``True`` if ``weekday`` is present in :attr:`working_days`.
        """
        return weekday.value in self.working_days

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return (
            f"<Shift id={self.id!r} name={self.name!r} "
            f"{self.start_time}-{self.end_time}>"
        )


class EmployeeShiftAssignment(CompanyScopedMixin, BaseModel):
    """A historical record of an employee being assigned to a shift.

    Rather than a single mutable ``shift_id`` column on
    :class:`~models.employee.Employee`, assignments are modeled as
    dated intervals so attendance calculations for a past date always
    use the shift that was actually in effect then, even after the
    employee has since moved to a different shift.

    Attributes:
        employee_id: The assigned employee.
        shift_id: The assigned shift.
        effective_from: First date this assignment applies.
        effective_to: Last date this assignment applies; ``NULL`` means
            it is the employee's current, open-ended assignment. The
            service layer is responsible for closing the previous open
            assignment (setting its ``effective_to``) whenever a new one
            is created for the same employee — the database does not
            enforce "at most one open assignment per employee", since a
            portable partial-unique-index is not available uniformly
            across SQLite, PostgreSQL and MySQL.
        notes: Optional free-form notes (e.g. reason for the change).
        employee: The related :class:`~models.employee.Employee`.
        shift: The related :class:`Shift`.
    """

    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_employee_shift_assignments_valid_range",
        ),
    )

    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shift_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shifts.id"), nullable=False, index=True
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    employee: Mapped["Employee"] = relationship(  # noqa: F821
        "Employee", backref="shift_assignments"
    )
    shift: Mapped["Shift"] = relationship("Shift", backref="assignments")

    @property
    def is_current(self) -> bool:
        """Whether this assignment is the employee's open-ended, active one."""
        return self.effective_to is None

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return (
            f"<EmployeeShiftAssignment id={self.id!r} "
            f"employee_id={self.employee_id!r} shift_id={self.shift_id!r} "
            f"from={self.effective_from!r} to={self.effective_to!r}>"
        )

"""Attendance policy profile ORM model — a reusable operational-rules template.

Reuses the same field vocabulary as the Attendance Client's own
:class:`~models.shift.Shift` (``grace_period_minutes``,
``early_leave_grace_minutes``, ``overtime_threshold_minutes``,
``working_days`` stored the identical way — a
:class:`~sqlalchemy.ext.mutable.MutableList`-wrapped JSON list of
:class:`~models.enums.Weekday` values) and
:class:`~models.enums.Weekday` itself directly, rather than inventing a
second, parallel vocabulary for the same concepts.
"""

from __future__ import annotations

from sqlalchemy import JSON, Integer, String
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column

from developer_suite.database.base import DeveloperSuiteBaseModel
from models.enums import Weekday

_DEFAULT_WORKING_DAYS: list[str] = [
    Weekday.SATURDAY.value,
    Weekday.SUNDAY.value,
    Weekday.MONDAY.value,
    Weekday.TUESDAY.value,
    Weekday.WEDNESDAY.value,
]


class AttendancePolicyProfile(DeveloperSuiteBaseModel):
    """A reusable set of attendance/shift operational defaults.

    Attributes:
        name: A unique, human-readable identifier for this profile.
        grace_period_minutes: Minutes an employee may check in late
            before being marked late.
        early_leave_grace_minutes: Minutes an employee may check out
            early without being marked as leaving early.
        overtime_threshold_minutes: Minutes past shift end before extra
            time counts as overtime.
        half_day_threshold_hours: Hours worked below which a day counts
            as a half day rather than a full day.
        working_days: :class:`~models.enums.Weekday` values (stored as
            their string codes, same representation as
            :attr:`~models.shift.Shift.working_days`) considered
            working days by default.
    """

    name: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    grace_period_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    early_leave_grace_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    overtime_threshold_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    half_day_threshold_hours: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    working_days: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON), default=lambda: list(_DEFAULT_WORKING_DAYS), nullable=False
    )

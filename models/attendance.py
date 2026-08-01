"""Attendance ORM models: raw punches and daily attendance records.

Modeled as two tiers, matching how real biometric attendance systems
actually work:

* :class:`AttendancePunch` — an append-only log of individual check-in
  / check-out events, from a manual entry or a biometric device import.
  This is the raw source-of-truth trail and is not meant to be edited.
* :class:`AttendanceRecord` — one row per employee per calendar day,
  aggregating that day's punches into the figures the rest of the
  system actually uses: check-in/out time, computed late/overtime/
  worked minutes, and a day :class:`~models.enums.AttendanceDayStatus`.
  This is the row HR edits when correcting a mistake, and the row
  reports are built from.

Computing late/overtime/worked minutes requires comparing punches
against the employee's :class:`~models.shift.Shift` for that day (and,
once built, leave/holiday overrides) — genuine business logic that
belongs in ``services/attendance_service.py``, not here. This module
only stores the computed results and exposes simple, self-contained
derived properties (unit conversions, boolean checks) that need no
cross-model lookups.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import BaseModel, CompanyScopedMixin, UTCDateTime, enum_column_type
from models.enums import AttendanceDayStatus, AttendanceSource, PunchType


class AttendanceRecord(CompanyScopedMixin, BaseModel):
    """One employee's aggregated attendance for a single calendar day.

    Attributes:
        employee_id: The employee this record belongs to.
        shift_id: The shift that was in effect for this employee on
            :attr:`work_date`, if known. Kept for reference even after
            the shift is later deleted (``ON DELETE SET NULL``) since
            the figures below have already been computed against it.
        work_date: The calendar day this record covers.
        check_in_time: Effective check-in timestamp for the day (first
            qualifying punch, or a manual correction).
        check_out_time: Effective check-out timestamp for the day.
        status: The computed :class:`~models.enums.AttendanceDayStatus`
            (present, late, absent, on leave, holiday, ...).
        late_minutes: Minutes late relative to the shift start, after
            grace period; ``0`` if not late.
        early_leave_minutes: Minutes left early relative to shift end,
            after grace period; ``0`` if not applicable.
        overtime_minutes: Minutes worked beyond the shift's overtime
            threshold; ``0`` if none.
        worked_minutes: Total minutes actually worked this day.
        is_manually_edited: Whether HR has corrected this record by
            hand after it was originally computed from device/manual
            punches — used to flag records for audit review.
        notes: Free-form notes (e.g. reason for a manual correction).
        employee: The related :class:`~models.employee.Employee`.
        shift: The related :class:`~models.shift.Shift`, if any.
        punches: Raw :class:`AttendancePunch` rows that contributed to
            this record.
    """

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "employee_id",
            "work_date",
            name="uq_attendance_records_company_employee_work_date",
        ),
        CheckConstraint("late_minutes >= 0", name="ck_attendance_records_late_minutes"),
        CheckConstraint(
            "early_leave_minutes >= 0", name="ck_attendance_records_early_leave_minutes"
        ),
        CheckConstraint(
            "overtime_minutes >= 0", name="ck_attendance_records_overtime_minutes"
        ),
        CheckConstraint("worked_minutes >= 0", name="ck_attendance_records_worked_minutes"),
    )

    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shift_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("shifts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    work_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    check_in_time: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    check_out_time: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    status: Mapped[AttendanceDayStatus] = mapped_column(
        enum_column_type(AttendanceDayStatus),
        nullable=False,
        default=AttendanceDayStatus.ABSENT,
        index=True,
    )

    late_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    early_leave_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    overtime_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    worked_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    is_manually_edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    employee: Mapped["Employee"] = relationship(  # noqa: F821
        "Employee", backref="attendance_records"
    )
    shift: Mapped["Shift | None"] = relationship("Shift")  # noqa: F821

    @property
    def worked_hours(self) -> float:
        """:attr:`worked_minutes` expressed in hours, rounded to 2 decimals."""
        return round(self.worked_minutes / 60, 2)

    @property
    def overtime_hours(self) -> float:
        """:attr:`overtime_minutes` expressed in hours, rounded to 2 decimals."""
        return round(self.overtime_minutes / 60, 2)

    @property
    def is_late(self) -> bool:
        """Whether this record has any recorded lateness."""
        return self.late_minutes > 0

    @property
    def has_overtime(self) -> bool:
        """Whether this record has any recorded overtime."""
        return self.overtime_minutes > 0

    @property
    def status_label_ar(self) -> str:
        """Arabic display label for :attr:`status`."""
        return self.status.label_ar

    @property
    def status_label_en(self) -> str:
        """English display label for :attr:`status`."""
        return self.status.label_en

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return (
            f"<AttendanceRecord id={self.id!r} employee_id={self.employee_id!r} "
            f"work_date={self.work_date!r} status={self.status.value!r}>"
        )


class AttendancePunch(CompanyScopedMixin, BaseModel):
    """A single raw check-in/check-out event.

    Append-only by convention: correcting attendance means editing the
    aggregated :class:`AttendanceRecord`, not rewriting history here.

    Attributes:
        employee_id: The employee who punched.
        device_id: The biometric device this punch came from, if any;
            ``NULL`` for a manual entry.
        attendance_record_id: The :class:`AttendanceRecord` this punch
            was aggregated into, once processed; ``NULL`` until the
            aggregation service has run for this day.
        punch_type: Whether this was a check-in or check-out.
        source: Where the punch originated
            (:class:`~models.enums.AttendanceSource`).
        punch_time: The moment the punch occurred (device clock or
            manual entry time), stored as UTC.
        device_user_reference: The raw user identifier as reported by
            the device (useful for troubleshooting a mismatched
            device-to-employee mapping during sync).
        notes: Free-form notes, typically only present on manual entries
            (e.g. reason for a manual punch).
        employee: The related :class:`~models.employee.Employee`.
        attendance_record: The related :class:`AttendanceRecord`, if
            aggregated.
    """

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "employee_id",
            "device_id",
            "punch_time",
            name="uq_attendance_punches_dedup",
        ),
    )

    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    attendance_record_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("attendance_records.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    punch_type: Mapped[PunchType] = mapped_column(enum_column_type(PunchType), nullable=False)
    source: Mapped[AttendanceSource] = mapped_column(
        enum_column_type(AttendanceSource), nullable=False, default=AttendanceSource.MANUAL
    )
    punch_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)

    device_user_reference: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    employee: Mapped["Employee"] = relationship(  # noqa: F821
        "Employee", backref="attendance_punches"
    )
    attendance_record: Mapped["AttendanceRecord | None"] = relationship(
        "AttendanceRecord", backref="punches"
    )

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return (
            f"<AttendancePunch id={self.id!r} employee_id={self.employee_id!r} "
            f"type={self.punch_type.value!r} time={self.punch_time!r}>"
        )

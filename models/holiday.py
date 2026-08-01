"""Official holiday calendar ORM model.

Each company maintains its own holiday calendar — Gregorian by default,
per this system's Iraq-focused locale defaults (see
``config.LocaleConfig``), with individual dates a company configures
itself so that culturally/regionally variable observances (e.g. Hijri
-calendar holidays that shift on the Gregorian calendar every year) are
simply entered as needed rather than hard-coded into the software.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from models.base import BaseModel, CompanyScopedMixin


class Holiday(CompanyScopedMixin, BaseModel):
    """A single official holiday on a company's attendance calendar.

    Attributes:
        name: Holiday name (e.g. ``"يوم التأسيس"``).
        holiday_date: The calendar date this holiday falls on. For a
            recurring holiday, only the month and day are meaningful —
            see :attr:`is_recurring_annually`.
        is_recurring_annually: Whether this holiday repeats every year
            on the same month/day (e.g. New Year's Day). When ``True``,
            a single row covers every future year — the service layer
            never needs to insert a new row per year. When ``False``,
            this row applies to :attr:`holiday_date` exactly once (used
            for holidays whose Gregorian date shifts year to year).
        is_active: Whether this holiday currently counts toward
            attendance calculations (a company can deactivate a holiday
            without losing its historical record).
        description: Optional free-form notes about the holiday.
    """

    __table_args__ = (
        UniqueConstraint(
            "company_id", "holiday_date", "name", name="uq_holidays_company_id_date_name"
        ),
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    is_recurring_annually: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    def occurs_on(self, check_date: date) -> bool:
        """Whether this holiday applies to ``check_date``.

        Args:
            check_date: The calendar date to test.

        Returns:
            For a recurring holiday, ``True`` if the month and day
            match, regardless of year. For a non-recurring holiday,
            ``True`` only for an exact date match.
        """
        if self.is_recurring_annually:
            return (self.holiday_date.month, self.holiday_date.day) == (
                check_date.month,
                check_date.day,
            )
        return self.holiday_date == check_date

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return (
            f"<Holiday id={self.id!r} name={self.name!r} "
            f"date={self.holiday_date!r} recurring={self.is_recurring_annually!r}>"
        )

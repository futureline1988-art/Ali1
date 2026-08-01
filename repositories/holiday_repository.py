"""Repository for :class:`~models.holiday.Holiday`."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.holiday import Holiday
from repositories.base_repository import CompanyScopedRepository


class HolidayRepository(CompanyScopedRepository[Holiday]):
    """Data access for :class:`~models.holiday.Holiday`, scoped to one company."""

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create a repository bound to ``session`` and ``company_id``."""
        super().__init__(session, model=Holiday, company_id=company_id)

    def list_active(self) -> list[Holiday]:
        """List every active holiday defined for this company.

        Returns:
            Holidays with :attr:`~models.holiday.Holiday.is_active` set,
            ordered by date.
        """
        statement = (
            select(Holiday)
            .where(
                Holiday.company_id == self.company_id,
                Holiday.is_active.is_(True),
                Holiday.is_deleted.is_(False),
            )
            .order_by(Holiday.holiday_date)
        )
        return list(self.session.execute(statement).scalars().all())

    def is_holiday(self, check_date: date) -> bool:
        """Whether ``check_date`` falls on any of this company's holidays.

        Matching (including the recurring-holiday month/day comparison)
        is delegated to :meth:`~models.holiday.Holiday.occurs_on` on
        each candidate row rather than expressed in SQL, since matching
        a recurring holiday across arbitrary years is not a portable
        single-column comparison — and a company's holiday list is
        small enough that fetching the active set and checking in
        Python is not a real cost.

        Args:
            check_date: The date to test.

        Returns:
            ``True`` if any active holiday occurs on that date.
        """
        return any(holiday.occurs_on(check_date) for holiday in self.list_active())

    def list_between(self, start_date: date, end_date: date) -> list[date]:
        """Resolve every holiday date (recurring or not) within a range.

        Args:
            start_date: First day to check (inclusive).
            end_date: Last day to check (inclusive).

        Returns:
            Every calendar date in the range that is a holiday, sorted
            ascending. A recurring holiday contributes one date per
            year it spans within the range.
        """
        holidays = self.list_active()
        matched_dates: list[date] = []
        current = start_date
        while current <= end_date:
            if any(holiday.occurs_on(current) for holiday in holidays):
                matched_dates.append(current)
            current += timedelta(days=1)
        return matched_dates

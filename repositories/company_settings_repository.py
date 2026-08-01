"""Repository for :class:`~models.company_settings.CompanySettings`.

A true 1:1 with :class:`~models.company.Company` (see that model's
``UNIQUE(company_id)`` constraint), so this repository's shape differs
slightly from the others: there is exactly one row to find, not a list.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.company_settings import CompanySettings
from repositories.base_repository import CompanyScopedRepository


class CompanySettingsRepository(CompanyScopedRepository[CompanySettings]):
    """Data access for :class:`~models.company_settings.CompanySettings`."""

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create a repository bound to ``session`` and ``company_id``."""
        super().__init__(session, model=CompanySettings, company_id=company_id)

    def get_for_company(self) -> CompanySettings | None:
        """Fetch this company's single settings row, if it has been created.

        Returns:
            The company's :class:`~models.company_settings.CompanySettings`
            row, or ``None`` if it has not been seeded yet.
        """
        statement = select(CompanySettings).where(
            CompanySettings.company_id == self.company_id,
            CompanySettings.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

    def get_or_create(self, **defaults: Any) -> CompanySettings:
        """Fetch this company's settings row, creating it if missing.

        Every :class:`~models.company_settings.CompanySettings` field
        has a sensible default (see that model), so a bare call with no
        ``defaults`` is enough to seed a new company with working
        settings immediately after it is created.

        Args:
            **defaults: Column overrides to apply only when a new row
                must be created (ignored if a row already exists).

        Returns:
            The existing or newly-created settings row.
        """
        existing = self.get_for_company()
        if existing is not None:
            return existing
        settings = CompanySettings(company_id=self.company_id, **defaults)
        return self.add(settings)

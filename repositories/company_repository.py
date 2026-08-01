"""Repository for :class:`~models.company.Company` — the tenant root.

Not company-scoped (a ``Company`` is not owned by a company), so this
extends :class:`~repositories.base_repository.BaseRepository` directly.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.company import Company
from repositories.base_repository import BaseRepository


class CompanyRepository(BaseRepository[Company]):
    """Data access for :class:`~models.company.Company`."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        super().__init__(session, model=Company)

    def get_by_name(self, name: str) -> Company | None:
        """Fetch a company by its unique display name.

        Args:
            name: The company's :attr:`~models.company.Company.name`.

        Returns:
            The matching company, or ``None``.
        """
        statement = select(Company).where(
            Company.name == name, Company.is_deleted.is_(False)
        )
        return self.session.execute(statement).scalar_one_or_none()

    def list_active(self) -> list[Company]:
        """List every active (non-deactivated, non-deleted) company.

        Returns:
            Companies with :attr:`~models.company.Company.is_active` set.
        """
        statement = select(Company).where(
            Company.is_active.is_(True), Company.is_deleted.is_(False)
        ).order_by(Company.name)
        return list(self.session.execute(statement).scalars().all())

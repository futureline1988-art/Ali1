"""Repository for :class:`~models.license.License`.

Not scoped via :class:`~repositories.base_repository.CompanyScopedRepository`
even though the model carries a ``company_id``: license lookups are
often the *first* step in resolving which company a request belongs to
(e.g. validating a license key before a company context even exists),
so ``company_id`` is taken as an explicit per-call argument instead of
being fixed at construction time.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.license import License
from repositories.base_repository import BaseRepository


class LicenseRepository(BaseRepository[License]):
    """Data access for :class:`~models.license.License`."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        super().__init__(session, model=License)

    def get_by_key(self, license_key: str) -> License | None:
        """Fetch a license by its globally-unique key.

        Args:
            license_key: The :attr:`~models.license.License.license_key`.

        Returns:
            The matching license, or ``None``.
        """
        statement = select(License).where(License.license_key == license_key)
        return self.session.execute(statement).scalar_one_or_none()

    def get_active_for_company(self, company_id: int) -> License | None:
        """Fetch a company's currently-active license, if any.

        Args:
            company_id: The owning company's id.

        Returns:
            The active license, or ``None`` if the company has none.
        """
        statement = select(License).where(
            License.company_id == company_id,
            License.is_active.is_(True),
            License.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

    def list_for_company(self, company_id: int) -> list[License]:
        """List every license (including expired/inactive ones) for a company.

        Args:
            company_id: The owning company's id.

        Returns:
            Matching licenses, most recently issued first.
        """
        statement = (
            select(License)
            .where(License.company_id == company_id, License.is_deleted.is_(False))
            .order_by(License.issued_at.desc())
        )
        return list(self.session.execute(statement).scalars().all())

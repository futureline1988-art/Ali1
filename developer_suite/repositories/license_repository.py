"""Repository for :class:`~developer_suite.models.license.IssuedLicense`."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from developer_suite.models.customer import Customer
from developer_suite.models.license import IssuedLicense
from developer_suite.repositories.base_repository import BaseRepository


class LicenseRepository(BaseRepository[IssuedLicense]):
    """Data access for :class:`~developer_suite.models.license.IssuedLicense`.

    Every read here eagerly loads :attr:`~developer_suite.models.license.IssuedLicense.customer`
    (``joinedload``) since callers — chiefly
    :class:`~developer_suite.ui.license_management_page.LicenseManagementPage` —
    read ``license.customer.company_name`` after the session that
    fetched it has already been closed by
    :meth:`~developer_suite.services.base_service.BaseService._session_scope`;
    an unloaded relationship on a detached instance would otherwise
    raise ``DetachedInstanceError``.
    """

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        super().__init__(session, model=IssuedLicense)

    def get_by_id(self, entity_id: int, *, include_deleted: bool = False) -> IssuedLicense | None:
        """Fetch a single license by primary key, with its customer eagerly loaded.

        Args:
            entity_id: The license's ``id``.
            include_deleted: Whether to return the row even if it has
                been soft-deleted.

        Returns:
            The matching license, or ``None`` if not found (or
            soft-deleted and ``include_deleted`` is ``False``).
        """
        statement = (
            select(IssuedLicense)
            .options(joinedload(IssuedLicense.customer))
            .where(IssuedLicense.id == entity_id)
        )
        if not include_deleted:
            statement = statement.where(IssuedLicense.is_deleted.is_(False))
        return self.session.execute(statement).scalar_one_or_none()

    def search(self, query: str, *, include_deleted: bool = False) -> list[IssuedLicense]:
        """Search issued licenses by customer company name or machine id.

        Args:
            query: A case-insensitive substring matched against the
                associated customer's
                :attr:`~developer_suite.models.customer.Customer.company_name`
                and the license's
                :attr:`~developer_suite.models.license.IssuedLicense.machine_id`.
                An empty/whitespace-only query returns every license.

        Returns:
            Matching licenses, most recently issued first.
        """
        stripped = query.strip()
        statement = (
            select(IssuedLicense)
            .join(Customer, IssuedLicense.customer_id == Customer.id)
            .options(joinedload(IssuedLicense.customer))
            .order_by(IssuedLicense.issued_at.desc())
        )
        if stripped:
            pattern = f"%{stripped}%"
            statement = statement.where(
                or_(
                    Customer.company_name.ilike(pattern),
                    IssuedLicense.machine_id.ilike(pattern),
                )
            )
        if not include_deleted:
            statement = statement.where(IssuedLicense.is_deleted.is_(False))
        return list(self.session.execute(statement).scalars().all())

    def list_by_customer(
        self, customer_id: int, *, include_deleted: bool = False
    ) -> list[IssuedLicense]:
        """List every license issued to one customer, most recent first.

        Args:
            customer_id: The customer to list licenses for.
            include_deleted: Whether to include soft-deleted rows.

        Returns:
            That customer's licenses, most recently issued first.
        """
        statement = (
            select(IssuedLicense)
            .options(joinedload(IssuedLicense.customer))
            .where(IssuedLicense.customer_id == customer_id)
            .order_by(IssuedLicense.issued_at.desc())
        )
        if not include_deleted:
            statement = statement.where(IssuedLicense.is_deleted.is_(False))
        return list(self.session.execute(statement).scalars().all())

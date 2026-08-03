"""Repository for :class:`~developer_suite.models.customer.Customer`."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from developer_suite.models.customer import Customer
from developer_suite.repositories.base_repository import BaseRepository


class CustomerRepository(BaseRepository[Customer]):
    """Data access for :class:`~developer_suite.models.customer.Customer`."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        super().__init__(session, model=Customer)

    def search(self, query: str, *, include_deleted: bool = False) -> list[Customer]:
        """Search customers by company or contact name.

        Args:
            query: A case-insensitive substring matched against
                :attr:`~developer_suite.models.customer.Customer.company_name`
                and
                :attr:`~developer_suite.models.customer.Customer.contact_name`.
                An empty/whitespace-only query returns every customer,
                matching :meth:`~developer_suite.repositories.base_repository.BaseRepository.list_all`.

        Returns:
            Matching customers, ordered by company name.
        """
        stripped = query.strip()
        statement = select(Customer).order_by(Customer.company_name)
        if stripped:
            pattern = f"%{stripped}%"
            statement = statement.where(
                or_(
                    Customer.company_name.ilike(pattern),
                    Customer.contact_name.ilike(pattern),
                )
            )
        if not include_deleted:
            statement = statement.where(Customer.is_deleted.is_(False))
        return list(self.session.execute(statement).scalars().all())

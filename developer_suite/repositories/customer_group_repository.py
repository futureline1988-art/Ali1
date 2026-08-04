"""Repository for :class:`~developer_suite.models.customer_group.CustomerGroup`."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from developer_suite.models.customer_group import CustomerGroup
from developer_suite.repositories.base_repository import BaseRepository


class CustomerGroupRepository(BaseRepository[CustomerGroup]):
    """Data access for :class:`~developer_suite.models.customer_group.CustomerGroup`."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        super().__init__(session, model=CustomerGroup)

    def get_by_id_with_customers(self, group_id: int) -> CustomerGroup | None:
        """Fetch one group with its member customers eagerly loaded."""
        statement = (
            select(CustomerGroup)
            .options(joinedload(CustomerGroup.customers))
            .where(CustomerGroup.id == group_id, CustomerGroup.is_deleted.is_(False))
        )
        return self.session.execute(statement).unique().scalar_one_or_none()

    def list_all_with_customers(self) -> list[CustomerGroup]:
        """List every group, ordered by name, with member customers eagerly loaded."""
        statement = (
            select(CustomerGroup)
            .options(joinedload(CustomerGroup.customers))
            .where(CustomerGroup.is_deleted.is_(False))
            .order_by(CustomerGroup.name)
        )
        return list(self.session.execute(statement).unique().scalars().all())

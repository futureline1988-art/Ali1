"""Repository for :class:`~server.models.initial_admin.InitialAdminAccount`."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.models.initial_admin import InitialAdminAccount
from server.repositories.base_repository import BaseRepository


class InitialAdminRepository(BaseRepository[InitialAdminAccount]):
    """Data access for :class:`~server.models.initial_admin.InitialAdminAccount`."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        super().__init__(session, model=InitialAdminAccount)

    def get_by_subscription_id(self, subscription_id: int) -> InitialAdminAccount | None:
        """Fetch the pending initial administrator for a subscription, if any.

        Args:
            subscription_id: The owning subscription's id.

        Returns:
            The matching, non-deleted row, or ``None``.
        """
        statement = select(InitialAdminAccount).where(
            InitialAdminAccount.subscription_id == subscription_id,
            InitialAdminAccount.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

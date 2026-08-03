"""Data access for :class:`~server.models.admin_account.AdminAccount`."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.models.admin_account import AdminAccount
from server.repositories.base_repository import BaseRepository


class AdminAccountRepository(BaseRepository[AdminAccount]):
    """Data access for admin accounts, bound to one session."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        super().__init__(session, model=AdminAccount)

    def get_by_username(self, username: str) -> AdminAccount | None:
        """Fetch a single account by its unique username.

        Args:
            username: The login name to look up.

        Returns:
            The matching account, or ``None`` if none exists (or it
            has been soft-deleted).
        """
        statement = select(AdminAccount).where(
            AdminAccount.username == username, AdminAccount.is_deleted.is_(False)
        )
        return self.session.execute(statement).scalar_one_or_none()

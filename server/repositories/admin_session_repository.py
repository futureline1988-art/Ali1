"""Data access for :class:`~server.models.admin_session.AdminSession`."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.models.admin_session import AdminSession
from server.repositories.base_repository import BaseRepository


class AdminSessionRepository(BaseRepository[AdminSession]):
    """Data access for admin login sessions, bound to one session."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        super().__init__(session, model=AdminSession)

    def list_for_account(self, account_id: int, *, only_active: bool = True) -> list[AdminSession]:
        """List an account's sessions, most recently created first.

        Args:
            account_id: The account to list sessions for.
            only_active: Whether to exclude revoked/expired sessions.

        Returns:
            Matching sessions.
        """
        statement = (
            select(AdminSession)
            .where(AdminSession.admin_account_id == account_id, AdminSession.is_deleted.is_(False))
            .order_by(AdminSession.id.desc())
        )
        if only_active:
            statement = statement.where(
                AdminSession.revoked_at.is_(None), AdminSession.expires_at > datetime.now(timezone.utc)
            )
        return list(self.session.execute(statement).scalars().all())

    def revoke_all_for_account(self, account_id: int) -> None:
        """Revoke every currently-active session belonging to ``account_id``.

        Used after a password change, so every other logged-in client
        is forced to re-authenticate with the new password.

        Args:
            account_id: The account whose sessions should be revoked.
        """
        now = datetime.now(timezone.utc)
        for session_row in self.list_for_account(account_id, only_active=True):
            session_row.revoked_at = now
        self.session.flush()

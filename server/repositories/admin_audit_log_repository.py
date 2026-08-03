"""Data access for :class:`~server.models.admin_audit_log.AdminAuditLog`."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.models.admin_audit_log import AdminAuditLog
from server.repositories.base_repository import BaseRepository


class AdminAuditLogRepository(BaseRepository[AdminAuditLog]):
    """Data access for the admin authentication audit trail, bound to one session."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        super().__init__(session, model=AdminAuditLog)

    def list_recent(self, *, account_id: int | None = None, limit: int = 100) -> list[AdminAuditLog]:
        """List the most recent audit events, most recent first.

        Args:
            account_id: Optionally restrict to one account's events.
            limit: Maximum number of rows to return.

        Returns:
            Matching audit log rows.
        """
        statement = (
            select(AdminAuditLog)
            .where(AdminAuditLog.is_deleted.is_(False))
            .order_by(AdminAuditLog.id.desc())
            .limit(limit)
        )
        if account_id is not None:
            statement = statement.where(AdminAuditLog.admin_account_id == account_id)
        return list(self.session.execute(statement).scalars().all())

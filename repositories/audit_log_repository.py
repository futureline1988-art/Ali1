"""Repository for :class:`~models.audit_log.AuditLog`.

Not scoped via :class:`~repositories.base_repository.CompanyScopedRepository`:
``AuditLog.company_id`` is itself nullable (platform-level events have
no owning tenant — see that model's docstring), so ``company_id`` is
taken as an optional per-call filter instead of a mandatory constructor
argument.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.audit_log import AuditLog
from repositories.base_repository import BaseRepository


class AuditLogRepository(BaseRepository[AuditLog]):
    """Data access for :class:`~models.audit_log.AuditLog`."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        super().__init__(session, model=AuditLog)

    def list_for_company(
        self, company_id: int | None, *, limit: int = 100
    ) -> list[AuditLog]:
        """List the most recent audit events for a company.

        Args:
            company_id: The company to filter by; ``None`` lists
                platform-level events that belong to no company.
            limit: Maximum number of rows to return.

        Returns:
            Matching entries, most recent first.
        """
        statement = (
            select(AuditLog)
            .where(AuditLog.company_id == company_id)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
        return list(self.session.execute(statement).scalars().all())

    def list_for_entity(self, entity_type: str, entity_id: int) -> list[AuditLog]:
        """List every audit event recorded against one specific record.

        Args:
            entity_type: The affected model's class name (e.g.
                ``"Employee"``).
            entity_id: The affected record's primary key.

        Returns:
            Matching entries, oldest first (a chronological history).
        """
        statement = (
            select(AuditLog)
            .where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
            .order_by(AuditLog.created_at.asc())
        )
        return list(self.session.execute(statement).scalars().all())

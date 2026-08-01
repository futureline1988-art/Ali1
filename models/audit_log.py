"""Audit/activity log ORM model for security tracking.

Unlike every other tenant-owned model in this project, ``AuditLog`` does
*not* inherit :class:`~models.base.CompanyScopedMixin`, because that
mixin makes ``company_id`` mandatory. Some events genuinely have no
single owning company — a platform-level action (e.g. an administrator
creating a new :class:`~models.company.Company`) or a failed login
attempt against a username that does not exist in any tenant — so
``company_id`` is declared directly here as a nullable foreign key
instead.

Rows in this table are append-only by convention: nothing in the
application should ever update or delete an audit log entry after it is
written.
"""

from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import BaseModel, enum_column_type
from models.enums import AuditAction


class AuditLog(BaseModel):
    """An immutable record of a security-relevant action.

    Attributes:
        company_id: The company this event relates to, if any;
            ``NULL`` for platform-level events not scoped to one tenant.
        user_id: The :class:`~models.user.User` who performed the
            action; ``NULL`` for system-initiated events (a scheduled
            backup job) or events where no valid user could be
            identified (e.g. a failed login against an unknown
            username).
        action: The kind of action performed
            (:class:`~models.enums.AuditAction`).
        entity_type: The affected model's class name (e.g.
            ``"Employee"``), for actions that target a specific record;
            ``NULL`` for actions with no single target (e.g. ``LOGIN``).
        entity_id: The affected record's primary key, when
            :attr:`entity_type` is set.
        description: Human-readable summary of what happened.
        changes: A JSON object describing what changed, typically shaped
            as ``{"field": {"old": ..., "new": ...}}``; populated for
            ``UPDATE`` actions, ``NULL`` otherwise.
        ip_address: Client IP address the action originated from, when
            known.
        user_agent: Client application/user-agent string, when known
            (primarily relevant once the REST API surface is in use).
        company: The related :class:`~models.company.Company`, if any.
        user: The related :class:`~models.user.User`, if any.
    """

    __table_args__ = (
        Index("ix_audit_logs_entity_type_entity_id", "entity_type", "entity_id"),
        Index("ix_audit_logs_company_id_created_at", "company_id", "created_at"),
    )

    company_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    # foreign_keys is required: AuditMixin already gives this table its
    # own created_by_id/updated_by_id -> users.id, so without it
    # SQLAlchemy would find multiple FK paths to 'users' and refuse to
    # guess which one this relationship means.
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    action: Mapped[AuditAction] = mapped_column(
        enum_column_type(AuditAction), nullable=False, index=True
    )
    entity_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    changes: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)

    company: Mapped["Company | None"] = relationship("Company")  # noqa: F821
    user: Mapped["User | None"] = relationship(  # noqa: F821
        "User", foreign_keys="AuditLog.user_id"
    )

    @property
    def action_label_ar(self) -> str:
        """Arabic display label for :attr:`action`."""
        return self.action.label_ar

    @property
    def action_label_en(self) -> str:
        """English display label for :attr:`action`."""
        return self.action.label_en

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return (
            f"<AuditLog id={self.id!r} action={self.action.value!r} "
            f"entity_type={self.entity_type!r} entity_id={self.entity_id!r}>"
        )

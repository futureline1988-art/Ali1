"""Append-only audit trail for admin authentication events.

Field shape and append-only discipline deliberately mirror the
Attendance Client's own ``models.audit_log.AuditLog`` — replicated,
not imported, since the two live in entirely separate schemas (see
``server/models/admin_account.py``'s docstring for why that pattern
recurs throughout this server's models). Rows are never updated or
deleted by any code in this codebase; a full history of every login
attempt, success or failure, is the entire point.
"""

from __future__ import annotations

from enum import Enum

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import enum_column_type
from server.database.base import ServerBaseModel


class AdminAuditAction(str, Enum):
    """What kind of authentication event one :class:`AdminAuditLog` row records."""

    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    TOKEN_REFRESH = "token_refresh"
    PASSWORD_CHANGE = "password_change"
    PASSWORD_RESET_REQUESTED = "password_reset_requested"
    PASSWORD_RESET_COMPLETED = "password_reset_completed"
    ACCOUNT_LOCKED = "account_locked"
    SESSION_REVOKED = "session_revoked"


class AdminAuditLog(ServerBaseModel):
    """One append-only record of an admin authentication event.

    Attributes:
        admin_account_id: Which account this event concerns; ``None``
            for a failed login against an unknown username (there is
            no account to attribute it to, but the attempt itself is
            still worth recording).
        action: What happened (see :class:`AdminAuditAction`).
        description: A short human-readable detail (e.g. why a login
            failed).
        ip_address: The caller's address, if known (not currently
            populated by any code in this codebase — a hook for a
            future phase that runs behind a reverse proxy forwarding
            it).
        user_agent: The caller's declared client label, if any.
    """

    admin_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[AdminAuditAction] = mapped_column(enum_column_type(AdminAuditAction), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)

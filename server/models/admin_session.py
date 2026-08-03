"""Admin login sessions: refresh-token records and session tracking, together.

A logged-in admin's "session" and its refresh token are the same
concept in this design: one :class:`AdminSession` row is created per
successful login, lives until it is revoked (logout, a password
change, or explicit revocation) or its :attr:`~AdminSession.expires_at`
passes, and *is* what :meth:`~server.services.admin_auth_service.AdminAuthService.list_sessions`
reports back to an account as its active sessions — a second,
separate "sessions" table tracking the same rows a refresh-token table
already tracks would be exactly the kind of duplicated bookkeeping
this platform's rules forbid.

The refresh token handed to a client is ``f"{session.public_id}.{secret}"``
— the same public-lookup-key / bcrypt-verified-secret split
:class:`~server.models.device.SyncDevice`'s ``X-Device-Id``/``X-Device-Api-Key``
pair already established for a bearer credential that must be looked
up before it can be verified (a bcrypt hash cannot be queried by
equality, since every hash of the same input differs). Rotated in
place on every successful refresh (a new secret, the same row and
``public_id``) rather than issuing a new row per refresh — simpler,
and avoids a window where both an old and a new row would validly
authenticate the same session.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import UTCDateTime
from server.database.base import ServerBaseModel
from server.models.admin_account import AdminAccount


class AdminSession(ServerBaseModel):
    """One admin account's login session, tracked via its refresh token.

    Attributes:
        admin_account_id: Which account this session belongs to.
        admin_account: The associated :class:`~server.models.admin_account.AdminAccount`.
        refresh_token_hash: Bcrypt hash of the session's current
            refresh secret (see :func:`utils.security.hash_password`)
            — rotated on every successful refresh.
        expires_at: When this session stops being valid outright, even
            if never revoked.
        revoked_at: Set on logout, a password change (which revokes
            every session for the account), or explicit revocation;
            ``None`` while still active.
        last_used_at: Timestamp of the most recent successful refresh
            (or the login that created this session, before any
            refresh has happened yet).
        user_agent: Optional caller-supplied label (e.g. a Developer
            Suite installation name), for a human reading the session
            list to recognize which device a session belongs to.
    """

    admin_account_id: Mapped[int] = mapped_column(
        ForeignKey("admin_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    admin_account: Mapped["AdminAccount"] = relationship("AdminAccount")

    refresh_token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)

    @property
    def is_valid(self) -> bool:
        """Whether this session can currently be used to refresh an access token."""
        if self.revoked_at is not None:
            return False
        return self.expires_at > datetime.now(timezone.utc)

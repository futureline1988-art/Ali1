"""Administrator account for the Attendance Server's own authentication system.

An admin account is a *person* logging into the Developer Suite (or any
future administrative client) with a username and password — a
completely different concept from :class:`~server.models.device.SyncDevice`
(a long-lived, non-interactive sync credential one installation holds).
The two are deliberately unrelated tables: nothing here participates in
the synchronization ledger, and nothing in :mod:`server.models.sync`
knows this table exists.

Field shape (``failed_login_attempts``/``locked_until``/``last_login_at``)
deliberately mirrors the Attendance Client's own ``models.user.User``
— the same lockout/last-login bookkeeping, replicated rather than
imported, since the two live in entirely separate schemas/databases
(see ``server/database/base.py``'s docstring on why a third,
independent ``Base`` exists at all). Password hashing itself is not
replicated: this model stores only ``password_hash``, produced and
verified exclusively through :func:`utils.security.hash_password`/
:func:`utils.security.verify_password`, the same primitive
``models.user.User``'s own bcrypt hash relies on.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import UTCDateTime, enum_column_type
from server.database.base import ServerBaseModel


class AdminRole(str, Enum):
    """Which fixed set of scopes a logged-in admin account's tokens carry.

    A small, explicit role -> scope mapping (see
    :data:`~server.services.admin_auth_service.ROLE_SCOPES`) rather
    than a full many-to-many role/permission schema — this server has
    exactly two real scopes in active use today
    (:data:`~server.auth.dependencies`'s ``"sync:admin"``, and the
    ``"sync:read"`` scope Phase 11 adds for read-only monitoring
    endpoints), so a fixed mapping is the honest amount of RBAC
    machinery for what actually exists, while staying a one-line
    extension point (add a scope to the mapping) if a future phase
    introduces more.
    """

    SUPER_ADMIN = "super_admin"
    VIEWER = "viewer"


class AdminAccount(ServerBaseModel):
    """A person authorized to administer the Attendance Server.

    Attributes:
        username: Unique login name.
        password_hash: Bcrypt hash of the account's password (see
            :func:`utils.security.hash_password`) — the plaintext
            password is never stored or logged anywhere.
        full_name: Display name.
        role: Determines this account's tokens' scopes (see
            :class:`AdminRole`).
        is_active: Whether this account can currently log in; set to
            ``False`` to deactivate without deleting (preserving audit
            history attribution).
        must_change_password: Set when an account is created or its
            password is reset by another party; the next successful
            login should prompt a change (enforcement is a caller
            concern — this column only records the fact).
        failed_login_attempts: Consecutive failed login attempts since
            the last success; reset on success.
        locked_until: Set once :attr:`failed_login_attempts` reaches
            :attr:`~config.SecurityConfig.max_login_attempts`; login is
            refused until this passes, even with the correct password.
        last_login_at: Timestamp of the most recent successful login.
    """

    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[AdminRole] = mapped_column(
        enum_column_type(AdminRole), nullable=False, default=AdminRole.VIEWER
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    @property
    def is_locked(self) -> bool:
        """Whether this account is currently locked out from repeated failed logins."""
        return self.locked_until is not None and self.locked_until > datetime.now(timezone.utc)

    @property
    def can_authenticate(self) -> bool:
        """Whether a login attempt for this account should be considered at all."""
        return self.is_active and not self.is_deleted and not self.is_locked

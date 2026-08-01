"""System user ORM model: authentication, authorization and session state.

A :class:`User` is an account that can log into this software (system
administrator, manager, HR officer, supervisor, or regular operator). It
is distinct from :class:`~models.employee.Employee`, which represents a
person whose attendance is tracked — the two may, but need not,
correspond to the same real individual.

Password hashing and verification are intentionally *not* implemented on
this model: those responsibilities belong to ``utils/security.py`` and
``services/auth_service.py``, keeping this class a pure persistence
entity plus a small amount of state-derived domain logic (lockout
status, role labels) that has no business living in a service.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import BaseModel, UTCDateTime, enum_column_type
from models.enums import UserRole


class User(BaseModel):
    """A login account for an operator of the attendance system.

    Attributes:
        username: Unique login handle.
        full_name: Display name; stores Arabic or English text as-is.
        email: Optional unique contact/recovery address.
        phone: Optional contact phone number.
        password_hash: A bcrypt hash produced by ``utils.security``;
            never a plaintext password.
        role: The :class:`~models.enums.UserRole` granted to this account.
        is_active: Whether the account may currently log in at all. This
            is independent of :attr:`~models.base.SoftDeleteMixin.is_deleted`
            — a deactivated account is disabled but still a "real" record,
            while a soft-deleted one has been removed from normal views.
        must_change_password: Forces a password reset on next login,
            typically set after an administrator-issued temporary
            password.
        preferred_language: The UI language this user last selected,
            either ``"ar"`` or ``"en"``.
        failed_login_attempts: Consecutive failed login count, reset on
            success; used to drive account lockout.
        locked_until: If set and in the future, login is refused
            regardless of credentials until this timestamp passes.
        last_login_at: Timestamp of the most recent successful login.
        notes: Free-form administrative notes about this account.
    """

    __table_args__ = (
        CheckConstraint(
            "preferred_language IN ('ar', 'en')",
            name="ck_users_preferred_language",
        ),
    )

    username: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    role: Mapped[UserRole] = mapped_column(
        enum_column_type(UserRole), nullable=False, default=UserRole.USER
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    preferred_language: Mapped[str] = mapped_column(
        String(2), default="ar", server_default="ar", nullable=False
    )

    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        UTCDateTime, default=None, nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, default=None, nullable=True
    )

    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    @property
    def is_locked(self) -> bool:
        """Whether this account is currently under a login lockout."""
        if self.locked_until is None:
            return False
        return self.locked_until > datetime.now(timezone.utc)

    @property
    def can_authenticate(self) -> bool:
        """Whether this account is eligible to attempt a login at all.

        Does not verify credentials — only that the account is active,
        not soft-deleted, and not currently locked out.
        """
        return self.is_active and not self.is_deleted and not self.is_locked

    @property
    def role_label_ar(self) -> str:
        """Arabic display label for :attr:`role`."""
        return self.role.label_ar

    @property
    def role_label_en(self) -> str:
        """English display label for :attr:`role`."""
        return self.role.label_en

    def register_successful_login(self) -> None:
        """Reset lockout/failure state and stamp the login time.

        Called by the authentication service after credentials have been
        verified successfully.
        """
        self.failed_login_attempts = 0
        self.locked_until = None
        self.last_login_at = datetime.now(timezone.utc)

    def register_failed_login(self, *, max_attempts: int, lockout_minutes: int) -> None:
        """Record a failed login attempt and lock the account if needed.

        Args:
            max_attempts: Number of consecutive failures allowed before
                the account is locked (see
                :attr:`config.SecurityConfig.max_login_attempts`).
            lockout_minutes: Lockout duration once ``max_attempts`` is
                reached (see
                :attr:`config.SecurityConfig.login_lockout_minutes`).
        """
        self.failed_login_attempts += 1
        if self.failed_login_attempts >= max_attempts:
            self.locked_until = datetime.now(timezone.utc) + timedelta(
                minutes=lockout_minutes
            )

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return (
            f"<User id={self.id!r} username={self.username!r} "
            f"role={self.role.value!r}>"
        )

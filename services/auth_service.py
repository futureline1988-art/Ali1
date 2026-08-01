"""Authentication service: login, logout, and password changes.

Owns exactly the business logic that :class:`~models.user.User` itself
deliberately does not: verifying credentials, deciding when to record a
failed attempt versus a lockout, and writing the audit trail. The model
only exposes the primitives (:meth:`~models.user.User.can_authenticate`,
:meth:`~models.user.User.register_successful_login`,
:meth:`~models.user.User.register_failed_login`) this service composes.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from config import get_config
from models.audit_log import AuditLog
from models.enums import AuditAction
from models.user import User
from repositories.audit_log_repository import AuditLogRepository
from repositories.user_repository import UserRepository
from utils.security import (
    SessionManager,
    hash_password,
    validate_password_strength,
    verify_password,
)


class AuthenticationError(Exception):
    """Raised when a login attempt or credential check fails."""


class AuthService:
    """Authentication operations scoped to one company.

    Attributes:
        session: The active database session.
        company_id: The company this service operates within.
        session_manager: Optional :class:`~utils.security.SessionManager`
            updated on successful login/logout; omit when this service
            is used for a one-off check (e.g. a password-verification
            dialog) that should not affect the app's active session.
    """

    def __init__(
        self,
        session: Session,
        *,
        company_id: int,
        session_manager: SessionManager | None = None,
    ) -> None:
        """Create an auth service bound to one session and company.

        Args:
            session: The active database session.
            company_id: The company to authenticate users against.
            session_manager: The desktop session tracker to update on
                login/logout, if any.
        """
        self.session = session
        self.company_id = company_id
        self.session_manager = session_manager
        self.user_repo = UserRepository(session, company_id=company_id)
        self.audit_repo = AuditLogRepository(session)

    def login(self, username: str, password: str) -> User:
        """Authenticate a user and, if a session manager is bound, start a session.

        Args:
            username: The login handle to authenticate.
            password: The plaintext password to verify.

        Returns:
            The authenticated :class:`~models.user.User`.

        Raises:
            AuthenticationError: If the username is unknown, the
                account cannot currently log in (inactive, deleted, or
                locked out), or the password is wrong. The message is
                deliberately generic for unknown-username and
                wrong-password cases, to avoid revealing which one
                occurred (a standard login-security practice); the
                account-lockout case is reported distinctly since the
                user legitimately needs to know why.
        """
        user = self.user_repo.get_by_username(username)
        if user is None:
            self._record_failed_login(username, user_id=None, reason="unknown_username")
            raise AuthenticationError("Invalid username or password.")

        if not user.is_active or user.is_deleted:
            self._record_failed_login(username, user_id=user.id, reason="inactive_account")
            raise AuthenticationError("Invalid username or password.")

        if user.is_locked:
            self._record_failed_login(username, user_id=user.id, reason="locked_out")
            raise AuthenticationError(
                "This account is temporarily locked due to repeated failed "
                "login attempts. Please try again later."
            )

        if not verify_password(password, user.password_hash):
            security_config = get_config().security
            user.register_failed_login(
                max_attempts=security_config.max_login_attempts,
                lockout_minutes=security_config.login_lockout_minutes,
            )
            self.session.flush()
            self._record_failed_login(username, user_id=user.id, reason="bad_password")
            raise AuthenticationError("Invalid username or password.")

        user.register_successful_login()
        self.session.flush()
        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=user.id,
                action=AuditAction.LOGIN,
                description=f"User {username!r} logged in.",
            )
        )
        if self.session_manager is not None:
            self.session_manager.start_session(user_id=user.id, company_id=self.company_id)
        return user

    def logout(self, user: User) -> None:
        """Log a user out, ending the bound session manager's session if any.

        Args:
            user: The user logging out.
        """
        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=user.id,
                action=AuditAction.LOGOUT,
                description=f"User {user.username!r} logged out.",
            )
        )
        if self.session_manager is not None:
            self.session_manager.end_session()

    def change_password(
        self, user: User, *, current_password: str, new_password: str
    ) -> None:
        """Change a user's password after verifying their current one.

        Args:
            user: The user changing their password.
            current_password: Their current plaintext password, for
                verification.
            new_password: The new plaintext password to set.

        Raises:
            AuthenticationError: If ``current_password`` does not match.
            ValueError: If ``new_password`` fails
                :func:`~utils.security.validate_password_strength`; the
                message lists every violation.
        """
        if not verify_password(current_password, user.password_hash):
            raise AuthenticationError("Current password is incorrect.")

        violations = validate_password_strength(new_password)
        if violations:
            raise ValueError(" ".join(violations))

        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        self.session.flush()

    def _record_failed_login(
        self, username: str, *, user_id: int | None, reason: str
    ) -> None:
        """Write a LOGIN_FAILED audit entry."""
        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=user_id,
                action=AuditAction.LOGIN_FAILED,
                description=f"Failed login for username={username!r} ({reason}).",
            )
        )

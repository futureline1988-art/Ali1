"""Admin authentication: login, refresh, logout, password change/reset, lockout, audit.

Reuses :func:`utils.security.hash_password`/:func:`utils.security.verify_password`/
:func:`utils.security.validate_password_strength`/:func:`utils.security.generate_session_token`
directly — the same bcrypt/strength/token primitives every other
credential in this codebase (Attendance Client user passwords, this
server's own :class:`~server.models.device.SyncDevice` API keys) is
built on — and :func:`server.auth.tokens.issue_token`, so every access
token this service mints flows through the exact same, already-tested
:func:`server.auth.dependencies.get_current_principal`/:func:`server.auth.dependencies.require_scope`
verification path every other admin-scoped endpoint already uses.
Nothing about how a bearer token is *verified* changes in this phase;
only a real way to *obtain* one is added.

Refresh tokens and password reset tokens are both
``f"{public_id}.{secret}"`` strings — the public half looks the row up
(a bcrypt hash cannot be queried by equality), the secret half is
bcrypt-verified against the row's stored hash — exactly the split
:class:`~server.models.device.SyncDevice`'s ``X-Device-Id``/
``X-Device-Api-Key`` pair already established.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from utils.security import (
    generate_session_token,
    hash_password,
    validate_password_strength,
    verify_password,
)

from database.database import Database
from server.auth.tokens import issue_token
from server.config import ServerConfig
from server.models.admin_account import AdminAccount, AdminRole
from server.models.admin_audit_log import AdminAuditAction, AdminAuditLog
from server.models.admin_password_reset import AdminPasswordResetToken
from server.models.admin_session import AdminSession
from server.repositories.admin_account_repository import AdminAccountRepository
from server.repositories.admin_audit_log_repository import AdminAuditLogRepository
from server.repositories.admin_password_reset_repository import AdminPasswordResetRepository
from server.repositories.admin_session_repository import AdminSessionRepository
from server.services.base_service import BaseService

#: Which scopes a token minted for each role carries. The only real
#: RBAC surface this server needs today — see
#: ``server/models/admin_account.py``'s ``AdminRole`` docstring for why
#: this is a fixed mapping rather than a full role/permission schema.
ROLE_SCOPES: dict[AdminRole, frozenset[str]] = {
    AdminRole.SUPER_ADMIN: frozenset({"sync:admin", "sync:read"}),
    AdminRole.VIEWER: frozenset({"sync:read"}),
}

_REFRESH_TOKEN_EXPIRY_DAYS = 30
_PASSWORD_RESET_TOKEN_EXPIRY_MINUTES = 30


class AdminAuthServiceError(Exception):
    """Base class for admin authentication failures the API layer should translate."""


class AdminAuthenticationError(AdminAuthServiceError):
    """The supplied username/password did not authenticate.

    Deliberately the same message for "unknown username," "wrong
    password," and "inactive account" — never reveals which, so a
    caller cannot enumerate valid usernames.
    """


class AccountLockedError(AdminAuthenticationError):
    """The account exists and the password may be correct, but it is currently locked out."""


class InvalidRefreshTokenError(AdminAuthServiceError):
    """The supplied refresh token is malformed, unknown, expired, or revoked."""


class InvalidResetTokenError(AdminAuthServiceError):
    """The supplied password reset token is malformed, unknown, expired, or already used."""


class PasswordPolicyError(AdminAuthServiceError):
    """A new password fails :func:`utils.security.validate_password_strength`."""


class AccountNotFoundError(AdminAuthServiceError):
    """No admin account exists with the given id."""


class SetupAlreadyCompletedError(AdminAuthServiceError):
    """First-run setup was attempted, but an admin account already exists."""


@dataclass(frozen=True)
class AuthResult:
    """The outcome of a successful login or token refresh.

    Attributes:
        account: The authenticated account.
        access_token: A short-lived bearer token for
            ``Authorization: Bearer <access_token>`` — verified by the
            existing, unmodified :func:`server.auth.dependencies.get_current_principal`.
        refresh_token: A long-lived credential for
            :meth:`AdminAuthService.refresh`; opaque to the caller
            beyond "store it and send it back later."
        expires_in_minutes: How long :attr:`access_token` remains
            valid for.
    """

    account: AdminAccount
    access_token: str
    refresh_token: str
    expires_in_minutes: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _split_token(token: str) -> tuple[uuid.UUID, str]:
    """Split a ``f"{public_id}.{secret}"`` token into its two halves.

    Raises:
        ValueError: ``token`` is malformed (no ``.``, or the first
            half is not a valid UUID).
    """
    public_id_str, separator, secret = token.partition(".")
    if not separator or not secret:
        raise ValueError("Token is missing its secret half.")
    return uuid.UUID(public_id_str), secret


class AdminAuthService(BaseService):
    """Authenticate admin accounts and manage their sessions."""

    def __init__(self, database: Database, *, config: ServerConfig) -> None:
        """Create a service bound to ``database`` and this server's configuration.

        Args:
            database: This server's own database.
            config: This server's configuration; supplies the bcrypt
                cost factor, lockout policy, password policy, and
                token signing secret/expiry.
        """
        super().__init__(database)
        self._config = config

    # ------------------------------------------------------------------
    # Account provisioning.
    # ------------------------------------------------------------------

    def needs_initial_setup(self) -> bool:
        """Whether no admin account exists yet — first-run setup should run.

        Safe to expose unauthenticated (see the ``/setup-status`` route
        in ``server/api/routers/auth.py``): the answer reveals nothing
        more sensitive than whether a login page would currently
        accept anyone at all, and a client needs exactly this to
        decide whether to show the setup wizard or the ordinary login
        screen (see ``developer_suite/ui/first_run_setup_window.py``).
        """
        with self._session_scope() as session:
            return AdminAccountRepository(session).count() == 0

    def bootstrap_first_admin(
        self, *, username: str, password: str, full_name: str | None = None
    ) -> AuthResult:
        """Create the very first admin account and start a session for it.

        The interactive replacement for the environment-variable
        bootstrap seeding this phase originally shipped with
        (``ATTENDANCE_SERVER_BOOTSTRAP_ADMIN_USERNAME``/``_PASSWORD``,
        since removed — see ``server/database/bootstrap.py``'s
        history): rather than a hidden default credential a deployment
        script might set (and a developer might reasonably not want
        living anywhere in the project, even as an env var convention),
        the very first person to launch the Developer Suite against a
        brand-new server is prompted to create this account themselves,
        through the same UI everyone else logs in through.

        Self-limiting rather than needing a separate authorization
        check: the account count is re-verified inside the same
        transaction that creates the new one, so this can only ever
        succeed once per deployment — including against a race between
        two people opening the wizard against the same fresh server at
        once, the loser of which gets :exc:`SetupAlreadyCompletedError`
        instead of a second super-admin account.

        Args:
            username: The new account's unique login name.
            password: The new account's initial plaintext password.
            full_name: Optional display name.

        Returns:
            A session for the new account, exactly like a successful
            :meth:`login` — the caller can go straight into the
            application rather than showing a second, separate login
            prompt right after setup.

        Raises:
            SetupAlreadyCompletedError: An admin account already
                exists; the caller should fall back to the ordinary
                login screen.
            PasswordPolicyError: ``password`` fails the configured
                strength policy.
        """
        self._validate_password_or_raise(password)
        with self._session_scope() as session:
            if AdminAccountRepository(session).count() > 0:
                raise SetupAlreadyCompletedError(
                    "An administrator account already exists; first-run setup is no longer available."
                )

            account = AdminAccount(
                username=username,
                password_hash=hash_password(password, rounds=self._config.security.bcrypt_rounds),
                full_name=full_name,
                role=AdminRole.SUPER_ADMIN,
            )
            AdminAccountRepository(session).add(account)

            refresh_token, _session_row = self._start_session(session, account, user_agent=None)
            self._audit(
                session,
                account.id,
                AdminAuditAction.ACCOUNT_CREATED_VIA_SETUP,
                "Initial administrator account created via first-run setup.",
            )
            return AuthResult(
                account=account,
                access_token=self._issue_access_token(account),
                refresh_token=refresh_token,
                expires_in_minutes=self._config.api.token_expires_minutes,
            )

    def create_account(
        self,
        *,
        username: str,
        password: str,
        full_name: str | None = None,
        role: AdminRole = AdminRole.VIEWER,
        must_change_password: bool = False,
    ) -> AdminAccount:
        """Create a new admin account.

        Args:
            username: The account's unique login name.
            password: The initial plaintext password (hashed
                immediately, never stored or logged in plaintext).
            full_name: Display name.
            role: Determines this account's tokens' scopes.
            must_change_password: Whether the next login should
                prompt a password change.

        Returns:
            The newly created account.

        Raises:
            PasswordPolicyError: ``password`` fails the configured
                strength policy.
        """
        self._validate_password_or_raise(password)
        with self._session_scope() as session:
            account = AdminAccount(
                username=username,
                password_hash=hash_password(password, rounds=self._config.security.bcrypt_rounds),
                full_name=full_name,
                role=role,
                must_change_password=must_change_password,
            )
            AdminAccountRepository(session).add(account)
            return account

    # ------------------------------------------------------------------
    # Login / refresh / logout.
    # ------------------------------------------------------------------

    def login(self, username: str, password: str, *, user_agent: str | None = None) -> AuthResult:
        """Authenticate a username/password pair and start a new session.

        Args:
            username: The login name supplied by the caller.
            password: The plaintext password supplied by the caller.
            user_agent: An optional caller-supplied label for the
                resulting session (see
                :attr:`~server.models.admin_session.AdminSession.user_agent`).

        Returns:
            The new access/refresh token pair.

        Raises:
            AccountLockedError: The account is currently locked out.
            AdminAuthenticationError: Any other authentication failure
                (unknown username, wrong password, inactive account).
        """
        # The failure paths below must still persist their bookkeeping
        # (failed_login_attempts, locked_until, the audit row) even
        # though the overall call raises — but _session_scope() rolls
        # back on any exception raised from inside its own `with`
        # block. So every branch below only ever returns a plain
        # result marker from inside the block (letting it commit
        # normally); the actual raise happens after the block has
        # already committed.
        with self._session_scope() as session:
            account = AdminAccountRepository(session).get_by_username(username)

            if account is None:
                self._audit(session, None, AdminAuditAction.LOGIN_FAILED, f"Unknown username {username!r}.")
                outcome: str | AuthResult = "invalid"
            elif account.is_locked:
                self._audit(session, account.id, AdminAuditAction.LOGIN_FAILED, "Account is locked.")
                outcome = "locked"
            elif not account.can_authenticate or not verify_password(password, account.password_hash):
                self._register_failed_login(session, account)
                outcome = "invalid"
            else:
                account.failed_login_attempts = 0
                account.locked_until = None
                account.last_login_at = _utc_now()
                session.flush()

                refresh_token, _session_row = self._start_session(session, account, user_agent=user_agent)
                self._audit(session, account.id, AdminAuditAction.LOGIN, "Login succeeded.")
                outcome = AuthResult(
                    account=account,
                    access_token=self._issue_access_token(account),
                    refresh_token=refresh_token,
                    expires_in_minutes=self._config.api.token_expires_minutes,
                )

        if outcome == "locked":
            raise AccountLockedError("This account is temporarily locked due to repeated failed logins.")
        if outcome == "invalid":
            raise AdminAuthenticationError("Invalid username or password.")
        return outcome

    def refresh(self, refresh_token: str) -> AuthResult:
        """Exchange a valid refresh token for a new access token, rotating the refresh token too.

        Args:
            refresh_token: A token previously returned by :meth:`login`
                or a prior :meth:`refresh` call.

        Returns:
            A new access/refresh token pair. The previous
            ``refresh_token`` is no longer valid after this call.

        Raises:
            InvalidRefreshTokenError: ``refresh_token`` is malformed,
                unknown, expired, revoked, or its account is no longer
                available.
        """
        try:
            public_id, secret = _split_token(refresh_token)
        except ValueError as exc:
            raise InvalidRefreshTokenError("Refresh token is malformed.") from exc

        with self._session_scope() as session:
            session_row = AdminSessionRepository(session).get_by_public_id(public_id)
            if session_row is None or not verify_password(secret, session_row.refresh_token_hash):
                raise InvalidRefreshTokenError("Refresh token is invalid.")
            if not session_row.is_valid:
                raise InvalidRefreshTokenError("Refresh token has expired or been revoked.")

            account = AdminAccountRepository(session).get_by_id(session_row.admin_account_id)
            if account is None or not account.can_authenticate:
                raise InvalidRefreshTokenError("This account is no longer available.")

            new_secret = generate_session_token()
            session_row.refresh_token_hash = hash_password(
                new_secret, rounds=self._config.security.bcrypt_rounds
            )
            session_row.expires_at = _utc_now() + timedelta(days=_REFRESH_TOKEN_EXPIRY_DAYS)
            session_row.last_used_at = _utc_now()
            session.flush()

            self._audit(session, account.id, AdminAuditAction.TOKEN_REFRESH, None)
            return AuthResult(
                account=account,
                access_token=self._issue_access_token(account),
                refresh_token=f"{session_row.public_id}.{new_secret}",
                expires_in_minutes=self._config.api.token_expires_minutes,
            )

    def logout(self, refresh_token: str) -> None:
        """Revoke one session by its refresh token.

        Silently a no-op for an already-invalid token — logging out
        twice, or logging out with a token that expired on its own,
        has nothing left to do and is not an error.

        Args:
            refresh_token: The session's current refresh token.
        """
        try:
            public_id, secret = _split_token(refresh_token)
        except ValueError:
            return

        with self._session_scope() as session:
            session_row = AdminSessionRepository(session).get_by_public_id(public_id)
            if session_row is None or not verify_password(secret, session_row.refresh_token_hash):
                return
            session_row.revoked_at = _utc_now()
            session.flush()
            self._audit(session, session_row.admin_account_id, AdminAuditAction.LOGOUT, None)

    def list_audit_log(self, *, limit: int = 100) -> list[AdminAuditLog]:
        """List the most recent admin authentication audit events, most recent first.

        Read-only reuse of :meth:`~server.repositories.admin_audit_log_repository.AdminAuditLogRepository.list_recent`
        — Phase 12's Developer Dashboard is the first caller, surfaced
        through a new read-only route (see
        ``server/api/routers/auth.py``); no new audit-writing logic is
        added here, only a read path over the trail every other method
        on this service already appends to.

        Args:
            limit: Maximum number of rows to return.

        Returns:
            The most recent audit log rows, across every account.
        """
        with self._session_scope() as session:
            return AdminAuditLogRepository(session).list_recent(limit=limit)

    def list_sessions(self, account_public_id: uuid.UUID) -> list[AdminSession]:
        """List an account's currently active sessions, most recent first.

        Args:
            account_public_id: The account to list sessions for,
                identified by its public id (the same id an
                :class:`~server.auth.dependencies.AuthenticatedPrincipal`'s
                ``principal_id`` carries for an ``"admin_account"``
                token).

        Raises:
            AccountNotFoundError: No account with that public id.
        """
        with self._session_scope() as session:
            account = AdminAccountRepository(session).get_by_public_id(account_public_id)
            if account is None:
                raise AccountNotFoundError(f"No admin account with public_id={account_public_id!r}.")
            return AdminSessionRepository(session).list_for_account(account.id)

    # ------------------------------------------------------------------
    # Password change / reset.
    # ------------------------------------------------------------------

    def change_password(
        self, account_public_id: uuid.UUID, *, current_password: str, new_password: str
    ) -> None:
        """Change an authenticated account's own password.

        Revokes every existing session for the account (including the
        one used to make this call) — the caller must log in again
        with the new password.

        Args:
            account_public_id: The account changing its password,
                identified by its public id (see :meth:`list_sessions`
                for why).
            current_password: Must match the account's current password.
            new_password: The new password to set.

        Raises:
            AccountNotFoundError: No account with that public id.
            AdminAuthenticationError: ``current_password`` is incorrect.
            PasswordPolicyError: ``new_password`` fails the configured
                strength policy.
        """
        with self._session_scope() as session:
            account = AdminAccountRepository(session).get_by_public_id(account_public_id)
            if account is None:
                raise AccountNotFoundError(f"No admin account with public_id={account_public_id!r}.")
            if not verify_password(current_password, account.password_hash):
                raise AdminAuthenticationError("Current password is incorrect.")
            self._validate_password_or_raise(new_password)

            account.password_hash = hash_password(new_password, rounds=self._config.security.bcrypt_rounds)
            account.must_change_password = False
            session.flush()

            AdminSessionRepository(session).revoke_all_for_account(account.id)
            self._audit(session, account.id, AdminAuditAction.PASSWORD_CHANGE, None)

    def request_password_reset(self, username: str) -> str | None:
        """Issue a password reset token for an account, if it exists.

        Args:
            username: The account's login name.

        Returns:
            The plaintext reset token (pass to
            :meth:`complete_password_reset`), or ``None`` if no
            account with that username exists — deliberately
            indistinguishable from the outside (the caller should
            respond identically either way) to avoid leaking which
            usernames are registered.
        """
        with self._session_scope() as session:
            account = AdminAccountRepository(session).get_by_username(username)
            if account is None:
                return None

            secret = generate_session_token()
            record = AdminPasswordResetToken(
                admin_account_id=account.id,
                token_hash=hash_password(secret, rounds=self._config.security.bcrypt_rounds),
                expires_at=_utc_now() + timedelta(minutes=_PASSWORD_RESET_TOKEN_EXPIRY_MINUTES),
            )
            AdminPasswordResetRepository(session).add(record)
            self._audit(session, account.id, AdminAuditAction.PASSWORD_RESET_REQUESTED, None)
            return f"{record.public_id}.{secret}"

    def complete_password_reset(self, reset_token: str, new_password: str) -> None:
        """Redeem a password reset token, setting a new password.

        Revokes every existing session for the account, same as
        :meth:`change_password`.

        Args:
            reset_token: A token previously returned by
                :meth:`request_password_reset`.
            new_password: The new password to set.

        Raises:
            InvalidResetTokenError: ``reset_token`` is malformed,
                unknown, expired, already used, or its account no
                longer exists.
            PasswordPolicyError: ``new_password`` fails the configured
                strength policy.
        """
        try:
            public_id, secret = _split_token(reset_token)
        except ValueError as exc:
            raise InvalidResetTokenError("Reset token is malformed.") from exc

        with self._session_scope() as session:
            record = AdminPasswordResetRepository(session).get_by_public_id(public_id)
            if record is None or not verify_password(secret, record.token_hash):
                raise InvalidResetTokenError("Reset token is invalid.")
            if not record.is_valid:
                raise InvalidResetTokenError("Reset token has expired or already been used.")

            self._validate_password_or_raise(new_password)

            account = AdminAccountRepository(session).get_by_id(record.admin_account_id)
            if account is None:
                raise InvalidResetTokenError("This account no longer exists.")

            account.password_hash = hash_password(new_password, rounds=self._config.security.bcrypt_rounds)
            account.must_change_password = False
            account.failed_login_attempts = 0
            account.locked_until = None
            record.used_at = _utc_now()
            session.flush()

            AdminSessionRepository(session).revoke_all_for_account(account.id)
            self._audit(session, account.id, AdminAuditAction.PASSWORD_RESET_COMPLETED, None)

    # ------------------------------------------------------------------
    # Internals.
    # ------------------------------------------------------------------

    def _validate_password_or_raise(self, password: str) -> None:
        violations = validate_password_strength(
            password, minimum_length=self._config.security.minimum_password_length
        )
        if violations:
            raise PasswordPolicyError("; ".join(violations))

    def _register_failed_login(self, session: Session, account: AdminAccount) -> None:
        """Record one failed login attempt, locking the account if the threshold is reached."""
        account.failed_login_attempts += 1
        locked_now = account.failed_login_attempts >= self._config.security.max_login_attempts
        if locked_now:
            account.locked_until = _utc_now() + timedelta(minutes=self._config.security.login_lockout_minutes)
        session.flush()

        self._audit(session, account.id, AdminAuditAction.LOGIN_FAILED, "Incorrect password.")
        if locked_now:
            self._audit(
                session, account.id, AdminAuditAction.ACCOUNT_LOCKED, "Locked after repeated failed logins."
            )

    def _start_session(
        self, session: Session, account: AdminAccount, *, user_agent: str | None
    ) -> tuple[str, AdminSession]:
        """Create a new :class:`~server.models.admin_session.AdminSession` row for ``account``."""
        secret = generate_session_token()
        session_row = AdminSession(
            admin_account_id=account.id,
            refresh_token_hash=hash_password(secret, rounds=self._config.security.bcrypt_rounds),
            expires_at=_utc_now() + timedelta(days=_REFRESH_TOKEN_EXPIRY_DAYS),
            last_used_at=_utc_now(),
            user_agent=user_agent,
        )
        AdminSessionRepository(session).add(session_row)
        return f"{session_row.public_id}.{secret}", session_row

    def _issue_access_token(self, account: AdminAccount) -> str:
        """Mint a short-lived access token for ``account``, via the existing token infrastructure."""
        scopes = sorted(ROLE_SCOPES.get(account.role, frozenset()))
        return issue_token(
            {"principal_id": str(account.public_id), "principal_type": "admin_account", "scopes": scopes},
            config=self._config,
        )

    def _audit(
        self, session: Session, account_id: int | None, action: AdminAuditAction, description: str | None
    ) -> None:
        AdminAuditLogRepository(session).add(
            AdminAuditLog(admin_account_id=account_id, action=action, description=description)
        )

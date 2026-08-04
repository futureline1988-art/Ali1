"""Real session management for the Developer Suite's admin authentication.

:class:`AdminSessionManager` is the concrete
:class:`~developer_suite.admin.token_provider.AdminTokenProvider`
implementation Phase 11 replaces
:class:`~developer_suite.admin.token_provider.ConfiguredAdminTokenProvider`
with: it drives real login/refresh/logout against
:class:`~developer_suite.admin.auth_client.AdminAuthClient`, keeps the
current access/refresh token pair in memory, refreshes the access token
automatically just before it would expire, and — when "remember me" was
selected — persists the refresh token (encrypted, via
:class:`~developer_suite.models.admin_session.AdminSessionRecord`) so
:meth:`try_auto_login` can silently re-establish a session on the next
application launch without prompting for credentials again.

Nothing downstream of :class:`~developer_suite.admin.token_provider.AdminTokenProvider`
(:class:`~developer_suite.admin.client.AdminApiClient`,
:class:`~developer_suite.services.dashboard_service.DashboardService`,
every monitoring/status UI page) needed to change for this replacement
— they all depend only on that abstraction's single ``get_token()``
method.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from database.database import Database
from developer_suite.admin.auth_client import (
    AdminAccountInfo,
    AdminAuthClient,
    AdminAuthClientError,
    AdminAuthResult,
)
from developer_suite.repositories.admin_session_repository import AdminSessionRecordRepository

#: Refresh the access token this long before it would actually expire,
#: so a call made right at the boundary never races an in-flight
#: expiry — the same defensive margin
#: :class:`~server.services.admin_auth_service.AdminAuthService` itself
#: has no need for (it always mints a token with the full configured
#: lifetime), but a *client* holding onto one for a while does.
_REFRESH_MARGIN = timedelta(seconds=60)


class AdminSessionManager:
    """Owns the current admin session's in-memory and persisted state."""

    def __init__(self, database: Database, auth_client: AdminAuthClient) -> None:
        """Create a session manager bound to this installation's own database.

        Args:
            database: The Developer Suite's own database — where a
                "remember me" session is persisted, encrypted.
            auth_client: Talks to the Attendance Server's
                ``/api/v1/auth/*`` endpoints.
        """
        self._database = database
        self._auth_client = auth_client
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._access_token_expires_at: datetime | None = None
        self._account: AdminAccountInfo | None = None
        self._remember_me = False

    @property
    def is_authenticated(self) -> bool:
        """Whether a session is currently active in memory."""
        return self._refresh_token is not None

    @property
    def current_account(self) -> AdminAccountInfo | None:
        """The currently authenticated account, or ``None`` if not signed in."""
        return self._account

    def needs_initial_setup(self) -> bool:
        """Whether the Attendance Server has no admin account yet.

        Meant to be called once at application startup, before
        :meth:`try_auto_login` — if this returns ``True``, the caller
        should show :class:`~developer_suite.ui.first_run_setup_window.FirstRunSetupWindow`
        instead of the ordinary login window.

        Fails open to ``False`` (never blocks startup behind a check
        that itself depends on connectivity): if the server cannot be
        reached, this looks exactly like "no setup needed" and the
        application falls through to its ordinary
        :meth:`try_auto_login`/login-window flow — the same
        offline-tolerant behavior every other startup path in this
        application already has. A genuinely fresh deployment that
        happens to be unreachable at this exact moment simply shows a
        connection error on the login screen instead of the setup
        wizard, which is corrected the next time the app is launched
        with the server reachable.

        Returns:
            ``True`` only if the server was reachable and confirmed no
            admin account exists yet.
        """
        try:
            return self._auth_client.get_setup_status()
        except AdminAuthClientError:
            return False

    def complete_first_run_setup(
        self,
        username: str,
        password: str,
        *,
        full_name: str | None = None,
        remember_me: bool = False,
    ) -> AdminAccountInfo:
        """Create the very first admin account and start a session for it.

        Mirrors :meth:`login`'s shape exactly — the returned session is
        immediately active, so the caller (the setup wizard) can go
        straight into the application rather than showing a second,
        separate login prompt right after setup.

        Args:
            username: The new account's unique login name.
            password: The new account's initial plaintext password.
            full_name: Optional display name.
            remember_me: Whether this session should survive an
                application restart (see :meth:`try_auto_login`).

        Returns:
            The newly created account.

        Raises:
            developer_suite.admin.auth_client.AdminAuthClientError: Any
                setup or connection failure — the caller (the setup
                window) is expected to catch this and display it. In
                particular,
                :class:`~developer_suite.admin.auth_client.AdminAuthSetupAlreadyCompletedError`
                means another client already completed setup first
                (the caller should direct the user to the ordinary
                login screen instead).
        """
        result = self._auth_client.setup_first_admin(username, password, full_name=full_name)
        self._apply_result(result)
        self._remember_me = remember_me
        if remember_me:
            self._persist(result)
        else:
            self._clear_persisted()
        return result.account

    def login(self, username: str, password: str, *, remember_me: bool = False) -> AdminAccountInfo:
        """Authenticate and start a new session.

        Args:
            username: The login name.
            password: The plaintext password.
            remember_me: Whether this session should survive an
                application restart (see :meth:`try_auto_login`).

        Returns:
            The authenticated account.

        Raises:
            developer_suite.admin.auth_client.AdminAuthClientError: Any
                authentication or connection failure — the caller (the
                login window) is expected to catch this and display it,
                the account remains signed out.
        """
        result = self._auth_client.login(username, password)
        self._apply_result(result)
        self._remember_me = remember_me
        if remember_me:
            self._persist(result)
        else:
            self._clear_persisted()
        return result.account

    def try_auto_login(self) -> bool:
        """Attempt to silently resume a previously "remembered" session.

        Meant to be called once at application startup, before showing
        the login window — if it returns ``True``, the login window can
        be skipped entirely.

        Returns:
            ``True`` if a remembered session was successfully resumed
            (this manager is now authenticated); ``False`` if there was
            nothing to resume, or the stored refresh token was no
            longer valid (expired, revoked, or the account is gone) —
            the stored record is cleared in that case, so a stale token
            is never retried.
        """
        with self._database.session_scope() as session:
            record = AdminSessionRecordRepository(session).get()
            stored_refresh_token = record.refresh_token if record and record.remember_me else None

        if not stored_refresh_token:
            return False

        try:
            result = self._auth_client.refresh(stored_refresh_token)
        except AdminAuthClientError:
            self._clear_persisted()
            return False

        self._apply_result(result)
        self._remember_me = True
        self._persist(result)
        return True

    def logout(self) -> None:
        """End the current session, both locally and on the server.

        Safe to call even if not currently authenticated. Best-effort
        against the server — a connection failure does not prevent the
        local session from being cleared, since the point of logging
        out is that this installation should stop presenting itself as
        authenticated regardless of whether the server could be
        reached to revoke the token.
        """
        if self._refresh_token:
            try:
                self._auth_client.logout(self._refresh_token)
            except AdminAuthClientError:
                pass
        self._clear_state()
        self._clear_persisted()

    def get_token(self) -> str | None:
        """Return a currently-valid access token, refreshing it first if needed.

        Implements :class:`~developer_suite.admin.token_provider.AdminTokenProvider`.
        If the access token is at or past its expiry (minus
        :data:`_REFRESH_MARGIN`), a refresh is attempted transparently;
        if that refresh fails (the session has expired or been revoked
        server-side), the session is cleared and ``None`` is returned —
        exactly the "session expiration handling" a caller like
        :class:`~developer_suite.admin.client.AdminApiClient` needs: it
        already treats ``None`` as "not configured" and reacts
        accordingly.

        Returns:
            A valid access token, or ``None`` if there is no active
            session.
        """
        if self._access_token is None or self._refresh_token is None:
            return None

        if self._access_token_expires_at is not None and datetime.now() >= (
            self._access_token_expires_at - _REFRESH_MARGIN
        ):
            try:
                result = self._auth_client.refresh(self._refresh_token)
            except AdminAuthClientError:
                self._clear_state()
                self._clear_persisted()
                return None
            self._apply_result(result)
            if self._remember_me:
                self._persist(result)

        return self._access_token

    def _apply_result(self, result: AdminAuthResult) -> None:
        self._access_token = result.access_token
        self._refresh_token = result.refresh_token
        self._access_token_expires_at = result.received_at + timedelta(minutes=result.expires_in_minutes)
        self._account = result.account

    def _persist(self, result: AdminAuthResult) -> None:
        with self._database.session_scope() as session:
            AdminSessionRecordRepository(session).save(
                username=result.account.username, refresh_token=result.refresh_token, remember_me=True
            )

    def _clear_persisted(self) -> None:
        with self._database.session_scope() as session:
            AdminSessionRecordRepository(session).clear()

    def _clear_state(self) -> None:
        self._access_token = None
        self._refresh_token = None
        self._access_token_expires_at = None
        self._account = None
        self._remember_me = False

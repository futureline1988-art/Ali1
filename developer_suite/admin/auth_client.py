"""HTTP client for the Attendance Server's admin authentication endpoints.

Mirrors :mod:`developer_suite.admin.client`'s shape (synchronous
``httpx.Client``, the same reasoning for why — see that module's own
docstring) but talks to ``/api/v1/auth/*`` instead of the read-only
monitoring endpoints: login, refresh, logout, and password change. This
is the one piece of the platform that actually proves a username and
password, replacing Phase 10's static bootstrap token — see
:mod:`developer_suite.admin.session_manager` for the session-state
layer built on top of this client, and
:mod:`developer_suite.admin.token_provider` for the abstraction that
insulates :class:`~developer_suite.admin.client.AdminApiClient` from
this whole authentication story.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import httpx

_DEFAULT_TIMEOUT_SECONDS = 10.0


class AdminAuthClientError(Exception):
    """Base class for every failure this module raises."""


class AdminAuthConnectionError(AdminAuthClientError):
    """The Attendance Server could not be reached at all."""


class AdminAuthInvalidCredentialsError(AdminAuthClientError):
    """The supplied username/password did not authenticate (401)."""


class AdminAuthAccountLockedError(AdminAuthClientError):
    """The account is currently locked out (423)."""


class AdminAuthInvalidTokenError(AdminAuthClientError):
    """The supplied refresh token (or access token, for ``change_password``) was rejected (401)."""


class AdminAuthForbiddenError(AdminAuthClientError):
    """The server understood the token but refused the operation (403)."""


class AdminAuthPasswordPolicyError(AdminAuthClientError):
    """The new password was rejected by the server's strength policy (422)."""


class AdminAuthSetupAlreadyCompletedError(AdminAuthClientError):
    """First-run setup was attempted, but an admin account already exists (409)."""


class AdminAuthServerError(AdminAuthClientError):
    """The server reached the request but returned an unexpected error status."""


@dataclass(frozen=True)
class AdminAccountInfo:
    """The authenticated account, as embedded in a login/refresh response."""

    public_id: str
    username: str
    full_name: str | None
    role: str
    must_change_password: bool

    @classmethod
    def from_json(cls, data: dict) -> "AdminAccountInfo":
        """Parse the ``"account"`` object of a login/refresh response."""
        return cls(
            public_id=data["public_id"],
            username=data["username"],
            full_name=data.get("full_name"),
            role=data["role"],
            must_change_password=data["must_change_password"],
        )


@dataclass(frozen=True)
class AdminAuthResult:
    """The outcome of a successful login or token refresh.

    Attributes:
        access_token: A short-lived bearer token for the Attendance
            Server's other administrative endpoints (see
            :class:`~developer_suite.admin.client.AdminApiClient`).
        refresh_token: A long-lived credential for :meth:`AdminAuthClient.refresh`.
        expires_in_minutes: How long :attr:`access_token` remains valid.
        account: The authenticated account.
        received_at: When this result was produced locally — the
            reference point :mod:`developer_suite.admin.session_manager`
            uses to compute when :attr:`access_token` actually expires,
            since this client has no way to independently verify a
            server-issued token's claims.
    """

    access_token: str
    refresh_token: str
    expires_in_minutes: int
    account: AdminAccountInfo
    received_at: datetime

    @classmethod
    def from_json(cls, data: dict, *, received_at: datetime) -> "AdminAuthResult":
        """Parse a login/refresh response body."""
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_in_minutes=data["expires_in_minutes"],
            account=AdminAccountInfo.from_json(data["account"]),
            received_at=received_at,
        )


class AdminAuthClient:
    """A client for the Attendance Server's ``/api/v1/auth/*`` endpoints."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Create a client bound to one server.

        Args:
            base_url: The Attendance Server's base URL.
            transport: Optional ``httpx`` transport override, for tests.
            timeout: Request timeout in seconds.
        """
        self._base_url = base_url
        self._transport = transport
        self._timeout = timeout

    def get_setup_status(self) -> bool:
        """Whether the Attendance Server has no admin account yet.

        Raises:
            AdminAuthConnectionError: The server could not be reached.
            AdminAuthServerError: Any other non-2xx response.
        """
        response = self._get("/api/v1/auth/setup-status")
        self._raise_for_unexpected_status(response)
        return bool(response.json()["setup_required"])

    def setup_first_admin(
        self, username: str, password: str, *, full_name: str | None = None
    ) -> AdminAuthResult:
        """Create the very first admin account and start a session for it.

        Args:
            username: The new account's unique login name.
            password: The new account's initial plaintext password.
            full_name: Optional display name.

        Raises:
            AdminAuthConnectionError: The server could not be reached.
            AdminAuthSetupAlreadyCompletedError: An admin account
                already exists (409) — the caller raced another
                client's setup and should fall back to the ordinary
                login screen.
            AdminAuthPasswordPolicyError: The password fails the
                server's strength policy (422).
            AdminAuthServerError: Any other non-2xx response.
        """
        response = self._post(
            "/api/v1/auth/setup",
            json={"username": username, "password": password, "full_name": full_name},
        )
        if response.status_code == 409:
            raise AdminAuthSetupAlreadyCompletedError(
                "An administrator account already exists; first-run setup is no longer available."
            )
        if response.status_code == 422:
            raise AdminAuthPasswordPolicyError("The password does not meet the required policy.")
        self._raise_for_unexpected_status(response)
        return AdminAuthResult.from_json(response.json(), received_at=datetime.now())

    def login(self, username: str, password: str) -> AdminAuthResult:
        """Authenticate and start a new session.

        Raises:
            AdminAuthConnectionError: The server could not be reached.
            AdminAuthAccountLockedError: The account is locked (423).
            AdminAuthInvalidCredentialsError: Any other authentication
                failure (401).
            AdminAuthServerError: Any other non-2xx response.
        """
        response = self._post("/api/v1/auth/login", json={"username": username, "password": password})
        if response.status_code == 423:
            raise AdminAuthAccountLockedError(
                "This account is temporarily locked due to repeated failed logins."
            )
        if response.status_code == 401:
            raise AdminAuthInvalidCredentialsError("Invalid username or password.")
        self._raise_for_unexpected_status(response)
        return AdminAuthResult.from_json(response.json(), received_at=datetime.now())

    def refresh(self, refresh_token: str) -> AdminAuthResult:
        """Exchange a refresh token for a new access/refresh token pair.

        Raises:
            AdminAuthConnectionError: The server could not be reached.
            AdminAuthInvalidTokenError: The refresh token is invalid,
                expired, or revoked (401).
            AdminAuthServerError: Any other non-2xx response.
        """
        response = self._post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        if response.status_code == 401:
            raise AdminAuthInvalidTokenError("Refresh token is invalid, expired, or revoked.")
        self._raise_for_unexpected_status(response)
        return AdminAuthResult.from_json(response.json(), received_at=datetime.now())

    def logout(self, refresh_token: str) -> None:
        """Revoke a session by its refresh token.

        Never raises for an already-invalid token — logging out is
        always safe to attempt, mirroring
        :meth:`~server.services.admin_auth_service.AdminAuthService.logout`'s
        own "always succeeds" contract.

        Raises:
            AdminAuthConnectionError: The server could not be reached.
        """
        self._post("/api/v1/auth/logout", json={"refresh_token": refresh_token})

    def change_password(self, access_token: str, *, current_password: str, new_password: str) -> None:
        """Change the authenticated account's own password.

        Raises:
            AdminAuthConnectionError: The server could not be reached.
            AdminAuthInvalidTokenError: The current password is wrong,
                or the access token is invalid (401).
            AdminAuthPasswordPolicyError: The new password fails the
                server's strength policy (422).
            AdminAuthServerError: Any other non-2xx response.
        """
        response = self._post(
            "/api/v1/auth/change-password",
            json={"current_password": current_password, "new_password": new_password},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code == 401:
            raise AdminAuthInvalidTokenError("Current password is incorrect, or the session has expired.")
        if response.status_code == 422:
            raise AdminAuthPasswordPolicyError("The new password does not meet the required policy.")
        self._raise_for_unexpected_status(response)

    def _get(self, path: str) -> httpx.Response:
        client = httpx.Client(base_url=self._base_url, transport=self._transport, timeout=self._timeout)
        try:
            try:
                return client.get(path)
            except httpx.TransportError as exc:
                raise AdminAuthConnectionError(f"Could not reach the Attendance Server: {exc}") from exc
        finally:
            client.close()

    def _post(self, path: str, *, json: dict, headers: dict | None = None) -> httpx.Response:
        client = httpx.Client(base_url=self._base_url, transport=self._transport, timeout=self._timeout)
        try:
            try:
                return client.post(path, json=json, headers=headers)
            except httpx.TransportError as exc:
                raise AdminAuthConnectionError(f"Could not reach the Attendance Server: {exc}") from exc
        finally:
            client.close()

    def _raise_for_unexpected_status(self, response: httpx.Response) -> None:
        if response.status_code == 403:
            raise AdminAuthForbiddenError(f"Request refused: {response.text}")
        if response.status_code >= 400:
            raise AdminAuthServerError(f"{response.status_code} from {response.request.url}: {response.text}")

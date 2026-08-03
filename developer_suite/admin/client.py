"""HTTP client for the Attendance Server's read-only administration endpoints.

Mirrors :mod:`developer_suite.sync.client`'s shape closely (synchronous
``httpx.Client``, the same reasoning for why — see that module's
docstring) but serves a different purpose: this client is for a human
looking at a dashboard, not a device pushing/pulling changes, and it
never writes anything. Two of its methods (:meth:`AdminApiClient.check_health`,
:meth:`AdminApiClient.get_version`) call the existing, unauthenticated
``/health``/``/version`` endpoints and need no token at all; the rest
call Phase 10's new ``sync:admin``-scoped endpoints and depend on an
:class:`~developer_suite.admin.token_provider.AdminTokenProvider` for
their bearer token — never a concrete token source, so this client
needs no change when the provider is eventually replaced by a real
login flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from developer_suite.admin.token_provider import AdminTokenProvider

_DEFAULT_TIMEOUT_SECONDS = 10.0

#: How long since a device's last successful push/pull before it is
#: considered offline for display purposes. A presentation-layer
#: judgment call, not a synchronization-protocol concept — the server
#: itself has no notion of "online"/"offline" (see
#: ``server/models/device.py``'s ``last_seen_at`` docstring), only a
#: raw timestamp.
ONLINE_THRESHOLD = timedelta(minutes=15)


class AdminApiError(Exception):
    """Base class for every failure this module raises."""


class AdminApiNotConfiguredError(AdminApiError):
    """No admin token is available yet (see :class:`~developer_suite.admin.token_provider.AdminTokenProvider`)."""


class AdminApiConnectionError(AdminApiError):
    """The Attendance Server could not be reached at all."""


class AdminApiAuthError(AdminApiError):
    """The server rejected the current admin token (401/403)."""


class AdminApiServerError(AdminApiError):
    """The server reached the request but returned an unexpected error status."""


@dataclass(frozen=True)
class DeviceInfo:
    """One registered device, as returned by ``GET /api/v1/devices``."""

    public_id: str
    name: str
    device_type: str
    is_active: bool
    last_seen_at: datetime | None
    created_at: datetime

    def is_online(self, *, now: datetime | None = None) -> bool:
        """Whether this device has been seen recently enough to consider it online.

        Args:
            now: The reference time to compare against; defaults to
                the current UTC time. Overridable for deterministic
                tests.

        Returns:
            ``False`` if this device has never been seen, is inactive,
            or its last activity is older than :data:`ONLINE_THRESHOLD`.
        """
        if not self.is_active or self.last_seen_at is None:
            return False
        reference = now if now is not None else datetime.now(timezone.utc)
        return (reference - self.last_seen_at) <= ONLINE_THRESHOLD

    @classmethod
    def from_json(cls, data: dict) -> "DeviceInfo":
        """Parse one item of a ``GET /api/v1/devices`` response's ``devices`` list."""
        return cls(
            public_id=data["public_id"],
            name=data["name"],
            device_type=data["device_type"],
            is_active=data["is_active"],
            last_seen_at=_parse_datetime(data["last_seen_at"]),
            created_at=_parse_datetime(data["created_at"]),
        )


@dataclass(frozen=True)
class ServerStatus:
    """The Attendance Server's own health, as returned by ``GET /api/v1/status``."""

    app_name: str
    app_version: str
    database_connected: bool
    uptime_seconds: float

    @classmethod
    def from_json(cls, data: dict) -> "ServerStatus":
        """Parse a ``GET /api/v1/status`` response body."""
        return cls(
            app_name=data["app_name"],
            app_version=data["app_version"],
            database_connected=data["database_connected"],
            uptime_seconds=data["uptime_seconds"],
        )


@dataclass(frozen=True)
class AuditLogEntry:
    """One admin authentication audit event, as returned by ``GET /api/v1/auth/audit-log``."""

    public_id: str
    admin_account_id: int | None
    action: str
    description: str | None
    created_at: datetime

    @classmethod
    def from_json(cls, data: dict) -> "AuditLogEntry":
        """Parse one item of a ``GET /api/v1/auth/audit-log`` response's ``entries`` list."""
        return cls(
            public_id=data["public_id"],
            admin_account_id=data.get("admin_account_id"),
            action=data["action"],
            description=data.get("description"),
            created_at=_parse_datetime(data["created_at"]),
        )


@dataclass(frozen=True)
class SyncActivityEntry:
    """One recent change record, as returned by ``GET /api/v1/sync/activity``."""

    id: int
    entity_type: str
    entity_id: str
    operation: str
    status: str
    conflict_reason: str | None
    device_id: int
    created_at: datetime

    @classmethod
    def from_json(cls, data: dict) -> "SyncActivityEntry":
        """Parse one item of a ``GET /api/v1/sync/activity`` response's ``changes`` list."""
        return cls(
            id=data["id"],
            entity_type=data["entity_type"],
            entity_id=data["entity_id"],
            operation=data["operation"],
            status=data["status"],
            conflict_reason=data["conflict_reason"],
            device_id=data["device_id"],
            created_at=_parse_datetime(data["created_at"]),
        )


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp string as produced by ``SerializationMixin.to_dict``."""
    if value is None:
        return None
    return datetime.fromisoformat(value)


class AdminApiClient:
    """A read-only client for the Attendance Server's monitoring/status endpoints."""

    def __init__(
        self,
        base_url: str,
        token_provider: AdminTokenProvider,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Create a client bound to one server and one token source.

        Args:
            base_url: The Attendance Server's base URL.
            token_provider: Supplies the ``sync:admin`` bearer token
                for every authenticated call (see
                :mod:`developer_suite.admin.token_provider`).
            transport: Optional ``httpx`` transport override, for
                tests.
            timeout: Request timeout in seconds.
        """
        self._base_url = base_url
        self._token_provider = token_provider
        self._transport = transport
        self._timeout = timeout

    def check_health(self) -> bool:
        """Whether the Attendance Server responds to an unauthenticated liveness probe.

        Never raises — a connection failure is simply "not healthy,"
        exactly what a status tile needs to render without a
        try/except at every call site.
        """
        try:
            with self._unauthenticated_client() as client:
                response = client.get("/health")
            return response.status_code == 200
        except httpx.TransportError:
            return False

    def get_version(self) -> dict | None:
        """Fetch ``GET /version`` (unauthenticated); ``None`` if unreachable.

        Returns:
            ``{"app_name": ..., "app_version": ...}``, or ``None``.
        """
        try:
            with self._unauthenticated_client() as client:
                response = client.get("/version")
        except httpx.TransportError:
            return None
        if response.status_code != 200:
            return None
        return response.json()

    def get_server_status(self) -> ServerStatus:
        """Fetch ``GET /api/v1/status`` (admin-scoped).

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response.
        """
        response = self._authenticated_get("/api/v1/status")
        return ServerStatus.from_json(response.json())

    def list_devices(self) -> list[DeviceInfo]:
        """Fetch ``GET /api/v1/devices`` (admin-scoped).

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response.
        """
        response = self._authenticated_get("/api/v1/devices")
        return [DeviceInfo.from_json(item) for item in response.json()["devices"]]

    def list_recent_activity(self, *, limit: int = 50) -> list[SyncActivityEntry]:
        """Fetch ``GET /api/v1/sync/activity`` (admin-scoped), most recent first.

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response.
        """
        response = self._authenticated_get("/api/v1/sync/activity", params={"limit": limit})
        return [SyncActivityEntry.from_json(item) for item in response.json()["changes"]]

    def list_audit_log(self, *, limit: int = 50) -> list[AuditLogEntry]:
        """Fetch ``GET /api/v1/auth/audit-log`` (admin-scoped), most recent first.

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response.
        """
        response = self._authenticated_get("/api/v1/auth/audit-log", params={"limit": limit})
        return [AuditLogEntry.from_json(item) for item in response.json()["entries"]]

    def _unauthenticated_client(self) -> httpx.Client:
        return httpx.Client(base_url=self._base_url, transport=self._transport, timeout=self._timeout)

    def _authenticated_get(self, path: str, *, params: dict | None = None) -> httpx.Response:
        token = self._token_provider.get_token()
        if not token:
            raise AdminApiNotConfiguredError(
                "No administrator token is configured; this installation cannot reach the "
                "Attendance Server's administration APIs yet."
            )
        client = httpx.Client(
            base_url=self._base_url,
            transport=self._transport,
            timeout=self._timeout,
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            try:
                response = client.get(path, params=params)
            except httpx.TransportError as exc:
                raise AdminApiConnectionError(f"Could not reach the Attendance Server: {exc}") from exc
        finally:
            client.close()

        if response.status_code in (401, 403):
            raise AdminApiAuthError(f"Administrator token rejected: {response.text}")
        if response.status_code >= 400:
            raise AdminApiServerError(f"{response.status_code} from {path}: {response.text}")
        return response

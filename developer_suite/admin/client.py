"""HTTP client for the Attendance Server's administration endpoints.

Mirrors :mod:`developer_suite.sync.client`'s shape closely (synchronous
``httpx.Client``, the same reasoning for why — see that module's
docstring) but serves a different purpose: this client is for a human
looking at a dashboard (and, as of Phase 14, managing software
updates), not a device pushing/pulling changes. Two of its methods
(:meth:`AdminApiClient.check_health`, :meth:`AdminApiClient.get_version`)
call the existing, unauthenticated ``/health``/``/version`` endpoints
and need no token at all; the rest call ``sync:admin``-scoped endpoints
and depend on an :class:`~developer_suite.admin.token_provider.AdminTokenProvider`
for their bearer token — never a concrete token source, so this client
needs no change when the provider is eventually replaced by a real
login flow.

Through Phase 13 this client was read-only; Phase 14 is the first to
add write methods (creating/publishing/rolling back a software
update), reusing the exact same authenticated-request plumbing
(:meth:`AdminApiClient._authenticated_request`) rather than building a
second HTTP client — the "reuse existing API client" requirement
Phase 14's own spec calls out explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

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


@dataclass(frozen=True)
class UpdatePackageInfo:
    """One package (setup or portable) belonging to an update version."""

    id: int
    package_type: str
    checksum_sha256: str
    size_bytes: int

    @classmethod
    def from_json(cls, data: dict) -> "UpdatePackageInfo":
        """Parse one item of an update version's ``packages`` list."""
        return cls(
            id=data["id"],
            package_type=data["package_type"],
            checksum_sha256=data["checksum_sha256"],
            size_bytes=data["size_bytes"],
        )


@dataclass(frozen=True)
class UpdateVersionInfo:
    """One software update version, as returned by ``/api/v1/updates/versions``."""

    id: int
    version: str
    release_notes: str | None
    min_supported_version: str | None
    update_type: str
    publish_status: str
    scheduled_at: datetime | None
    published_at: datetime | None
    created_by: str
    created_at: datetime

    @classmethod
    def from_json(cls, data: dict) -> "UpdateVersionInfo":
        """Parse one update version response body."""
        return cls(
            id=data["id"],
            version=data["version"],
            release_notes=data.get("release_notes"),
            min_supported_version=data.get("min_supported_version"),
            update_type=data["update_type"],
            publish_status=data["publish_status"],
            scheduled_at=_parse_datetime(data.get("scheduled_at")),
            published_at=_parse_datetime(data.get("published_at")),
            created_by=data["created_by"],
            created_at=_parse_datetime(data["created_at"]),
        )


@dataclass(frozen=True)
class UpdateDeviceStatusInfo:
    """One device's reported status for one software update version.

    As returned by ``GET /api/v1/updates/device-status`` (Phase 15) —
    ``device_public_id``/``update_version_id`` are left as raw ids,
    resolved against :meth:`~AdminApiClient.list_devices`/
    :meth:`~AdminApiClient.list_update_versions` by whoever wants a
    friendly name, the same convention
    :class:`SyncActivityEntry.device_id` already established.
    """

    device_public_id: str
    update_version_id: int
    status: str
    progress_percent: int
    error_message: str | None
    reported_at: datetime

    @classmethod
    def from_json(cls, data: dict) -> "UpdateDeviceStatusInfo":
        """Parse one item of a ``GET /api/v1/updates/device-status`` response's ``statuses`` list."""
        return cls(
            device_public_id=data["device_public_id"],
            update_version_id=data["update_version_id"],
            status=data["status"],
            progress_percent=data["progress_percent"],
            error_message=data.get("error_message"),
            reported_at=_parse_datetime(data["reported_at"]),
        )


@dataclass(frozen=True)
class UpdateStatsInfo:
    """Update-distribution dashboard statistics, as returned by ``/api/v1/updates/stats``."""

    latest_deployed_version: str | None
    companies_per_version: dict[str, int]
    pending_count: int
    failed_count: int
    successful_count: int
    average_download_progress_percent: float | None

    @classmethod
    def from_json(cls, data: dict) -> "UpdateStatsInfo":
        """Parse a ``GET /api/v1/updates/stats`` response body."""
        return cls(
            latest_deployed_version=data.get("latest_deployed_version"),
            companies_per_version=data.get("companies_per_version") or {},
            pending_count=data["pending_count"],
            failed_count=data["failed_count"],
            successful_count=data["successful_count"],
            average_download_progress_percent=data.get("average_download_progress_percent"),
        )


@dataclass(frozen=True)
class SubscriptionInfo:
    """One company subscription, as returned by ``/api/v1/subscriptions``.

    The server-managed replacement for the retired file-based license
    system — see :mod:`server.models.subscription`.
    """

    id: int
    company_name: str
    subscription_start_date: date
    subscription_end_date: date
    status: str
    max_devices: int
    max_users: int | None
    is_active: bool
    is_expired: bool
    days_remaining: int
    device_count: int | None
    created_at: datetime

    @classmethod
    def from_json(cls, data: dict) -> "SubscriptionInfo":
        """Parse one subscription response body."""
        return cls(
            id=data["id"],
            company_name=data["company_name"],
            subscription_start_date=date.fromisoformat(data["subscription_start_date"]),
            subscription_end_date=date.fromisoformat(data["subscription_end_date"]),
            status=data["status"],
            max_devices=data["max_devices"],
            max_users=data.get("max_users"),
            is_active=data["is_active"],
            is_expired=data["is_expired"],
            days_remaining=data["days_remaining"],
            device_count=data.get("device_count"),
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

    # -- Software updates (Phase 14) -----------------------------------------

    def create_update_version(
        self,
        *,
        version: str,
        release_notes: str | None,
        min_supported_version: str | None,
        update_type: str,
    ) -> UpdateVersionInfo:
        """Create a new, draft update version.

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response (e.g. a
                duplicate version string, 409).
        """
        response = self._authenticated_request(
            "POST",
            "/api/v1/updates/versions",
            json={
                "version": version,
                "release_notes": release_notes,
                "min_supported_version": min_supported_version,
                "update_type": update_type,
            },
        )
        return UpdateVersionInfo.from_json(response.json())

    def upload_update_package(
        self,
        update_version_id: int,
        *,
        package_type: str,
        file_bytes: bytes,
        checksum_sha256: str,
        signature_base64: str,
        original_filename: str,
    ) -> UpdatePackageInfo:
        """Upload a setup or portable package for a version.

        ``checksum_sha256``/``signature_base64`` must already be
        computed by the caller (see
        :mod:`developer_suite.services.update_manager_service`, which
        signs the file with the Developer Suite's own update-signing
        private key before ever calling this method) — this client
        sends them as-is, over the raw-body-plus-headers convention
        :mod:`server.api.routers.updates` documents.

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response (e.g. a
                checksum mismatch, 400).
        """
        response = self._authenticated_request(
            "POST",
            f"/api/v1/updates/versions/{update_version_id}/packages",
            content=file_bytes,
            headers={
                "X-Package-Type": package_type,
                "X-Checksum-Sha256": checksum_sha256,
                "X-Signature-Base64": signature_base64,
                "X-Original-Filename": original_filename,
            },
        )
        return UpdatePackageInfo.from_json(response.json())

    def set_update_targets(
        self, update_version_id: int, *, scope: str, device_public_ids: list[str] | None = None
    ) -> None:
        """Replace a version's current targeting (every device, or a specific list).

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response.
        """
        self._authenticated_request(
            "PUT",
            f"/api/v1/updates/versions/{update_version_id}/targets",
            json={"scope": scope, "device_public_ids": device_public_ids or []},
        )

    def publish_update(self, update_version_id: int) -> UpdateVersionInfo:
        """Publish a version immediately.

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response (e.g. no
                package uploaded yet, 400).
        """
        response = self._authenticated_request("POST", f"/api/v1/updates/versions/{update_version_id}/publish")
        return UpdateVersionInfo.from_json(response.json())

    def schedule_update(self, update_version_id: int, *, scheduled_at: datetime) -> UpdateVersionInfo:
        """Schedule a version to become available at a future time.

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response.
        """
        response = self._authenticated_request(
            "POST",
            f"/api/v1/updates/versions/{update_version_id}/schedule",
            json={"scheduled_at": scheduled_at.isoformat()},
        )
        return UpdateVersionInfo.from_json(response.json())

    def disable_update(self, update_version_id: int) -> UpdateVersionInfo:
        """Disable a version, removing it from every latest/assigned response.

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response.
        """
        response = self._authenticated_request("POST", f"/api/v1/updates/versions/{update_version_id}/disable")
        return UpdateVersionInfo.from_json(response.json())

    def rollback_update(self, update_version_id: int, *, reason: str | None = None) -> UpdateVersionInfo:
        """Roll back a version: never deletes it, only excludes it from future offers.

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response.
        """
        response = self._authenticated_request(
            "POST", f"/api/v1/updates/versions/{update_version_id}/rollback", json={"reason": reason}
        )
        return UpdateVersionInfo.from_json(response.json())

    def list_update_versions(self) -> list[UpdateVersionInfo]:
        """List every update version regardless of status, most recently created first.

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response.
        """
        response = self._authenticated_get("/api/v1/updates/versions")
        return [UpdateVersionInfo.from_json(item) for item in response.json()["versions"]]

    def get_update_version_detail(self, update_version_id: int) -> dict:
        """Fetch one version's full detail: metadata, packages, targets, and audit history.

        Returns:
            ``{"version": UpdateVersionInfo, "packages": [...], "targets": [...],
            "rollbacks": [...], "audit_events": [...]}`` — only
            ``"version"`` is parsed into a dataclass; the rest are the
            server's raw JSON dicts, since nothing outside the Update
            Manager page's own detail view needs them structured.

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response.
        """
        response = self._authenticated_get(f"/api/v1/updates/versions/{update_version_id}")
        data = response.json()
        return {
            "version": UpdateVersionInfo.from_json(data["version"]),
            "packages": data["packages"],
            "targets": data["targets"],
            "rollbacks": data["rollbacks"],
            "audit_events": data["audit_events"],
        }

    def get_update_stats(self) -> UpdateStatsInfo:
        """Fetch ``GET /api/v1/updates/stats`` (admin-scoped) for the Developer Dashboard.

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response.
        """
        response = self._authenticated_get("/api/v1/updates/stats")
        return UpdateStatsInfo.from_json(response.json())

    def list_update_device_statuses(self) -> list[UpdateDeviceStatusInfo]:
        """Fetch ``GET /api/v1/updates/device-status`` (admin-scoped) for the Update Deployment report.

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response.
        """
        response = self._authenticated_get("/api/v1/updates/device-status")
        return [UpdateDeviceStatusInfo.from_json(item) for item in response.json()["statuses"]]

    # -- Company subscriptions (server-managed licensing replacement) --------

    def create_subscription(
        self,
        *,
        company_name: str,
        subscription_start_date: date,
        subscription_end_date: date,
        max_devices: int,
        max_users: int | None = None,
    ) -> SubscriptionInfo:
        """Create a new company subscription.

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response (e.g. a
                duplicate company name, 409).
        """
        response = self._authenticated_request(
            "POST",
            "/api/v1/subscriptions",
            json={
                "company_name": company_name,
                "subscription_start_date": subscription_start_date.isoformat(),
                "subscription_end_date": subscription_end_date.isoformat(),
                "max_devices": max_devices,
                "max_users": max_users,
            },
        )
        return SubscriptionInfo.from_json(response.json())

    def list_subscriptions(self) -> list[SubscriptionInfo]:
        """Fetch every subscription, each with its current device count.

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response.
        """
        response = self._authenticated_get("/api/v1/subscriptions")
        return [SubscriptionInfo.from_json(item) for item in response.json()["subscriptions"]]

    def get_subscription(self, subscription_id: int) -> SubscriptionInfo:
        """Fetch a single subscription by id.

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response (e.g. 404).
        """
        response = self._authenticated_get(f"/api/v1/subscriptions/{subscription_id}")
        return SubscriptionInfo.from_json(response.json())

    def renew_subscription(self, subscription_id: int, *, new_end_date: date) -> SubscriptionInfo:
        """Extend a subscription's end date, without changing its suspend/active status.

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response (e.g. 404).
        """
        response = self._authenticated_request(
            "PATCH",
            f"/api/v1/subscriptions/{subscription_id}",
            json={"subscription_end_date": new_end_date.isoformat()},
        )
        return SubscriptionInfo.from_json(response.json())

    def suspend_subscription(self, subscription_id: int) -> SubscriptionInfo:
        """Suspend a subscription immediately.

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response (e.g. 404).
        """
        response = self._authenticated_request(
            "PATCH", f"/api/v1/subscriptions/{subscription_id}", json={"action": "suspend"}
        )
        return SubscriptionInfo.from_json(response.json())

    def reactivate_subscription(self, subscription_id: int) -> SubscriptionInfo:
        """Reactivate a suspended subscription.

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response (e.g. 404).
        """
        response = self._authenticated_request(
            "PATCH", f"/api/v1/subscriptions/{subscription_id}", json={"action": "reactivate"}
        )
        return SubscriptionInfo.from_json(response.json())

    def update_subscription_limits(
        self, subscription_id: int, *, max_devices: int | None = None, max_users: int | None = None
    ) -> SubscriptionInfo:
        """Change a subscription's device/user caps.

        Args:
            subscription_id: The subscription to update.
            max_devices: New device cap, or ``None`` to leave unchanged.
            max_users: New user cap, or ``None`` to leave unchanged
                (use :meth:`clear_subscription_max_users` to explicitly
                set it back to unlimited).

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response (e.g. 404).
        """
        body: dict[str, object] = {}
        if max_devices is not None:
            body["max_devices"] = max_devices
        if max_users is not None:
            body["max_users"] = max_users
        response = self._authenticated_request(
            "PATCH", f"/api/v1/subscriptions/{subscription_id}", json=body
        )
        return SubscriptionInfo.from_json(response.json())

    def clear_subscription_max_users(self, subscription_id: int) -> SubscriptionInfo:
        """Explicitly set a subscription's user cap back to unlimited.

        Raises:
            AdminApiNotConfiguredError: No admin token is available.
            AdminApiConnectionError: The server could not be reached.
            AdminApiAuthError: The token was rejected.
            AdminApiServerError: Any other non-2xx response (e.g. 404).
        """
        response = self._authenticated_request(
            "PATCH",
            f"/api/v1/subscriptions/{subscription_id}",
            json={"max_users_unlimited": True},
        )
        return SubscriptionInfo.from_json(response.json())

    def _unauthenticated_client(self) -> httpx.Client:
        return httpx.Client(base_url=self._base_url, transport=self._transport, timeout=self._timeout)

    def _authenticated_get(self, path: str, *, params: dict | None = None) -> httpx.Response:
        return self._authenticated_request("GET", path, params=params)

    def _authenticated_request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        token = self._token_provider.get_token()
        if not token:
            raise AdminApiNotConfiguredError(
                "No administrator token is configured; this installation cannot reach the "
                "Attendance Server's administration APIs yet."
            )
        request_headers = {"Authorization": f"Bearer {token}"}
        if headers:
            request_headers.update(headers)
        client = httpx.Client(
            base_url=self._base_url,
            transport=self._transport,
            timeout=self._timeout,
            headers=request_headers,
        )
        try:
            try:
                response = client.request(method, path, params=params, json=json, content=content)
            except httpx.TransportError as exc:
                raise AdminApiConnectionError(f"Could not reach the Attendance Server: {exc}") from exc
        finally:
            client.close()

        if response.status_code in (401, 403):
            raise AdminApiAuthError(f"Administrator token rejected: {response.text}")
        if response.status_code >= 400:
            raise AdminApiServerError(f"{response.status_code} from {path}: {response.text}")
        return response

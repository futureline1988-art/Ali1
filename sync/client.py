"""HTTP client for the Attendance Server's device registration and pull endpoints.

Pull-only, mirroring :mod:`developer_suite.sync.client`'s generic
shape but deliberately omitting ``push``/``ChangeToPush``/``PushResultItem``
entirely: this installation never sends local changes to the server in
this phase (see :mod:`sync` package docstring). Uses ``httpx`` for the
same reasons :mod:`developer_suite.sync.client` does — already a
required project dependency, and every other synchronous call site in
this codebase is plain session-per-call code with no event loop
running.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from sync.protocol import DeviceType, SyncOperation

_DEFAULT_TIMEOUT_SECONDS = 10.0


class SyncClientError(Exception):
    """Base class for every failure this module raises."""


class SyncConnectionError(SyncClientError):
    """The Attendance Server could not be reached at all (DNS, refused, timed out, ...)."""


class SyncAuthError(SyncClientError):
    """The server rejected the supplied device credential or bearer token (401)."""


class SyncServerError(SyncClientError):
    """The server reached the request but returned an unexpected error status."""


@dataclass(frozen=True)
class PulledChange:
    """One applied change returned by ``GET /sync/pull``."""

    change_record_id: int
    entity_type: str
    entity_id: str
    operation: SyncOperation
    payload: dict
    new_version: int | None

    @classmethod
    def from_json(cls, data: dict) -> "PulledChange":
        """Parse one item of a pull response's ``changes`` list."""
        return cls(
            change_record_id=data["id"],
            entity_type=data["entity_type"],
            entity_id=data["entity_id"],
            operation=SyncOperation(data["operation"]),
            payload=data["payload"],
            new_version=data["new_version"],
        )


@dataclass(frozen=True)
class PullBatch:
    """One batch of pulled changes plus the cursor to resume from next time."""

    changes: list[PulledChange] = field(default_factory=list)
    next_cursor: int = 0


def _raise_for_response(response: httpx.Response) -> None:
    """Translate a non-2xx response into the appropriate :class:`SyncClientError`."""
    if response.status_code == 401:
        raise SyncAuthError(f"Authentication rejected: {response.text}")
    if response.status_code >= 400:
        raise SyncServerError(f"{response.status_code} from {response.request.url}: {response.text}")


def register_device(
    base_url: str,
    admin_bearer_token: str,
    *,
    name: str,
    device_type: DeviceType = DeviceType.ATTENDANCE_CLIENT,
    transport: httpx.BaseTransport | None = None,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> tuple[str, str]:
    """Register this installation as a new device and return its sync credential.

    A one-time enrollment call — see
    :meth:`~sync.coordinator.ClientSyncCoordinator.enroll` for what
    persists the result locally.

    Args:
        base_url: The Attendance Server's base URL.
        admin_bearer_token: A token with the ``sync:admin`` scope.
        name: A human-readable label for this installation.
        device_type: Which application this device is — always
            :attr:`~sync.protocol.DeviceType.ATTENDANCE_CLIENT` here.
        transport: Optional ``httpx`` transport override, for tests.
        timeout: Request timeout in seconds.

    Returns:
        ``(device_public_id, api_key)`` — ``api_key`` is shown exactly
        once and must be persisted by the caller.

    Raises:
        SyncConnectionError: The server could not be reached.
        SyncAuthError: The bearer token was rejected.
        SyncServerError: Any other non-2xx response.
    """
    client = httpx.Client(base_url=base_url, transport=transport, timeout=timeout)
    try:
        try:
            response = client.post(
                "/api/v1/devices/register",
                json={"name": name, "device_type": device_type.value},
                headers={"Authorization": f"Bearer {admin_bearer_token}"},
            )
        except httpx.TransportError as exc:
            raise SyncConnectionError(f"Could not reach {base_url}: {exc}") from exc
        _raise_for_response(response)
        data = response.json()
        return data["device"]["public_id"], data["api_key"]
    finally:
        client.close()


class SyncClient:
    """A thin, generic HTTP client for one authenticated device's pull calls.

    Holds no state about what entity types it carries — every call site
    supplies its own ``entity_type``. See :mod:`sync.coordinator` for
    the orchestration layer built on top of this.
    """

    def __init__(
        self,
        base_url: str,
        *,
        device_public_id: str,
        device_api_key: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Create a client bound to one device's credentials.

        Args:
            base_url: The Attendance Server's base URL.
            device_public_id: This device's UUID (``X-Device-Id``).
            device_api_key: This device's plaintext sync credential
                (``X-Device-Api-Key``).
            transport: Optional ``httpx`` transport override, for
                tests.
            timeout: Request timeout in seconds.
        """
        self._client = httpx.Client(
            base_url=base_url,
            transport=transport,
            timeout=timeout,
            headers={"X-Device-Id": device_public_id, "X-Device-Api-Key": device_api_key},
        )

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> "SyncClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def pull(self, since: int, *, entity_type: str | None = None, limit: int = 100) -> PullBatch:
        """Pull one batch of applied changes after a cursor.

        Args:
            since: Resume after this change id (``0`` for the very
                beginning).
            entity_type: Restrict to one entity type. Always pass the
                same value a given local cursor was last advanced
                with.
            limit: Maximum number of changes to return.

        Returns:
            The pulled batch.

        Raises:
            SyncConnectionError: The server could not be reached.
            SyncAuthError: This device's credential was rejected.
            SyncServerError: Any other non-2xx response.
        """
        params: dict[str, str | int] = {"since": since, "limit": limit}
        if entity_type is not None:
            params["entity_type"] = entity_type
        try:
            response = self._client.get("/api/v1/sync/pull", params=params)
        except httpx.TransportError as exc:
            raise SyncConnectionError(f"Could not reach the Attendance Server: {exc}") from exc
        _raise_for_response(response)
        data = response.json()
        changes = [PulledChange.from_json(item) for item in data["changes"]]
        return PullBatch(changes=changes, next_cursor=data["next_cursor"])

"""HTTP client for the Attendance Server's device registration and sync endpoints.

Uses ``httpx`` rather than ``requests`` (this project's usual HTTP
client — see ``requirements.txt``'s "external sync" note on
``requests``) for its cleaner, typed response/transport API and
because it is already a required project dependency (``fastapi.testclient.TestClient``
is itself built on it). Deliberately synchronous
(:class:`httpx.Client`, not ``AsyncClient``): every other Developer
Suite service in this codebase is a plain synchronous,
session-per-call class (see
:mod:`developer_suite.services.base_service`'s docstring), and this
client is called from exactly that kind of code — introducing
``asyncio`` here alone, with nothing else in the application running
an event loop, would be a bigger and stranger change than the sync
call style it would replace.

One consequence worth noting: ``httpx.ASGITransport`` (an in-process,
no-socket way to route requests straight into a FastAPI app) only
implements the *async* transport interface, so it cannot back a
synchronous :class:`httpx.Client`. :mod:`tests.test_phase8_customer_sync`
instead runs the real Attendance Server app under a real ``uvicorn``
server, on an OS-assigned loopback port, in a background thread —
exercising this exact client class over a genuine socket, which
``base_url``-only production code paths already assumed anyway.

Every method here only ever knows about the two generic sync
endpoints (``/api/v1/sync/push``, ``/api/v1/sync/pull``) and device
registration — nothing here has ever heard of a customer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from developer_suite.sync.protocol import ChangeStatus, DeviceType, SyncOperation

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
class ChangeToPush:
    """One local change ready to send in a push request body.

    Mirrors :class:`server.services.sync_service.ChangeInput`'s shape
    exactly — the wire format is a protocol contract, not a shared
    class (see :mod:`developer_suite.sync.protocol`'s docstring).
    """

    entity_type: str
    entity_id: str
    operation: SyncOperation
    payload: dict
    checksum: str
    base_version: int

    def to_json(self) -> dict:
        """Render this change as one entry of a push request's ``changes`` list."""
        return {
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "operation": self.operation.value,
            "payload": self.payload,
            "checksum": self.checksum,
            "base_version": self.base_version,
        }


@dataclass(frozen=True)
class PushResultItem:
    """The server's outcome for one pushed change, as returned by ``POST /sync/push``."""

    entity_type: str
    entity_id: str
    status: ChangeStatus
    new_version: int | None
    conflict_reason: str | None
    change_record_id: int

    @classmethod
    def from_json(cls, data: dict) -> "PushResultItem":
        """Parse one item of a push response's ``results`` list."""
        return cls(
            entity_type=data["entity_type"],
            entity_id=data["entity_id"],
            status=ChangeStatus(data["status"]),
            new_version=data["new_version"],
            conflict_reason=data["conflict_reason"],
            change_record_id=data["change_record_id"],
        )


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
        """Parse one item of a pull response's ``changes`` list.

        The server's ``ChangeRecord.to_dict()`` includes every mapped
        column (``status``, ``checksum``, ``device_id``, timestamps,
        ...); only the fields an applier actually needs are kept here.
        """
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
    device_type: DeviceType,
    transport: httpx.BaseTransport | None = None,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> tuple[str, str]:
    """Register this installation as a new device and return its sync credential.

    A one-time enrollment call — see
    :meth:`~developer_suite.sync.coordinator.SyncCoordinator.enroll`
    for what persists the result locally. Requires an administrative
    bearer token (``sync:admin`` scope), since
    ``POST /api/v1/devices/register`` is an administrative endpoint on
    the server (see ``server/api/routers/devices.py``'s docstring for
    why: no interactive admin login flow exists yet for the Developer
    Suite to obtain one itself, so this token is supplied by whatever
    first-run setup process performs the enrollment).

    Args:
        base_url: The Attendance Server's base URL.
        admin_bearer_token: A token with the ``sync:admin`` scope.
        name: A human-readable label for this installation.
        device_type: Which application this device is.
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
    """A thin, generic HTTP client for one authenticated device's push/pull calls.

    Holds no state about what entity types it carries — every call
    site supplies its own ``entity_type``. See
    :mod:`developer_suite.sync.coordinator` for the orchestration layer
    built on top of this.
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
                tests (see this module's docstring).
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

    def push(self, changes: list[ChangeToPush]) -> list[PushResultItem]:
        """Push a batch of local changes; returns one result per change, in order.

        Args:
            changes: The batch to push, ``1``-``500`` items.

        Returns:
            One :class:`PushResultItem` per input change, in the same
            order.

        Raises:
            SyncConnectionError: The server could not be reached.
            SyncAuthError: This device's credential was rejected.
            SyncServerError: Any other non-2xx response.
        """
        try:
            response = self._client.post(
                "/api/v1/sync/push", json={"changes": [change.to_json() for change in changes]}
            )
        except httpx.TransportError as exc:
            raise SyncConnectionError(f"Could not reach the Attendance Server: {exc}") from exc
        _raise_for_response(response)
        return [PushResultItem.from_json(item) for item in response.json()["results"]]

    def pull(self, since: int, *, entity_type: str | None = None, limit: int = 100) -> PullBatch:
        """Pull one batch of applied changes after a cursor.

        Args:
            since: Resume after this change id (``0`` for the very
                beginning).
            entity_type: Restrict to one entity type. Always pass the
                same value a given local cursor was last advanced
                with — see
                :class:`~developer_suite.models.sync_state.SyncCursor`'s
                docstring for why mixing filters against one cursor is
                unsafe.
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

"""Pull-and-apply orchestration for the Attendance Client's own database.

The pull-only counterpart to
:mod:`developer_suite.sync.coordinator.SyncCoordinator` — this
installation never pushes local changes to the Attendance Server in
this phase (see :mod:`sync` package docstring), so this class has no
outbox/push machinery at all, only :meth:`enroll`, :meth:`is_enrolled`,
and :meth:`pull_and_apply`.

Device targeting: the generic ``GET /api/v1/sync/pull`` endpoint has no
per-device filtering (see
:mod:`server.services.sync_service.SyncService.pull_changes`) — a
published configuration change's ``entity_id`` is the *target*
device's ``public_id`` (the addressing convention
:mod:`developer_suite.services.configuration_publish_service`
establishes on the publish side). This coordinator pulls every change
of ``entity_type`` and then filters, in Python, to only the ones whose
``entity_id`` equals this installation's own enrolled
``device_public_id`` — changes addressed to any other installation are
skipped, and the cursor still advances past them (the same "seen and
deliberately ignored" contract
:meth:`developer_suite.sync.coordinator.SyncCoordinator.pull_and_apply`
already documents for an entity type with no registered applier).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from database.database import Database
from repositories.sync_repository import ClientSyncCredentialRepository, ClientSyncCursorRepository
from sync.client import SubscriptionStatusResult, SyncClient, register_device
from sync.protocol import DeviceType


class DeviceNotEnrolledError(Exception):
    """Raised by :meth:`ClientSyncCoordinator.pull_and_apply` before this installation has enrolled."""


@dataclass(frozen=True)
class PullSummary:
    """The outcome of one :meth:`ClientSyncCoordinator.pull_and_apply` call."""

    applied: int = 0
    skipped_other_device: int = 0
    next_cursor: int = 0


class ClientSyncCoordinator:
    """Owns this installation's enrollment and pull-and-apply loop.

    Constructed once at startup (see ``main.py``) and handed to
    :class:`~sync.scheduler.ClientSyncSchedulerService`, which drives
    :meth:`pull_and_apply` on a timer — the same "constructed once,
    driven by a scheduler" shape
    :class:`~developer_suite.sync.coordinator.SyncCoordinator` uses.
    """

    def __init__(
        self,
        database: Database,
        server_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Create a coordinator bound to ``database`` and ``server_url``.

        Args:
            database: The Attendance Client's own database.
            server_url: The Attendance Server's base URL, used only
                for :meth:`enroll` (once enrolled, the credential's
                own stored ``server_url`` is used for every pull —
                see :meth:`~sync.coordinator.ClientSyncCoordinator._build_client`).
            transport: Optional ``httpx`` transport override, forwarded
                to every :class:`~sync.client.SyncClient` this
                coordinator builds — for tests only.
        """
        self._database = database
        self._server_url = server_url
        self._transport = transport

    def enroll(self, *, admin_bearer_token: str, name: str, company_name: str | None = None) -> None:
        """Register this installation with the Attendance Server and persist its credential.

        Args:
            admin_bearer_token: A token with the ``sync:admin`` scope
                (see :func:`~sync.client.register_device`).
            name: A human-readable label for this installation.
            company_name: The exact ``Subscription.company_name`` this
                installation belongs to — required for this
                installation's subscription to be checkable via
                :meth:`get_subscription_status` (see
                :mod:`server.api.routers.subscriptions`).
        """
        device_public_id, api_key = register_device(
            self._server_url,
            admin_bearer_token,
            name=name,
            device_type=DeviceType.ATTENDANCE_CLIENT,
            company_name=company_name,
            transport=self._transport,
        )
        with self._database.session_scope() as session:
            ClientSyncCredentialRepository(session).save(
                device_public_id=device_public_id, api_key=api_key, server_url=self._server_url
            )

    def is_enrolled(self) -> bool:
        """Whether this installation has already enrolled with the Attendance Server."""
        with self._database.session_scope() as session:
            return ClientSyncCredentialRepository(session).get() is not None

    def _build_client(self, session: Session) -> SyncClient:
        credential = ClientSyncCredentialRepository(session).get()
        if credential is None:
            raise DeviceNotEnrolledError(
                "This installation has not enrolled with the Attendance Server yet; call enroll() first."
            )
        return SyncClient(
            credential.server_url,
            device_public_id=credential.device_public_id,
            device_api_key=credential.api_key,
            transport=self._transport,
        )

    def get_subscription_status(self) -> SubscriptionStatusResult:
        """Fetch this installation's own subscription status from the Attendance Server.

        Raises:
            DeviceNotEnrolledError: This installation has not enrolled
                yet.
            ~sync.client.SyncClientError: The server could not be
                reached, rejected this device's credential, or
                returned an unexpected error — see
                :meth:`~sync.client.SyncClient.get_subscription_status`.
        """
        with self._database.session_scope() as session:
            client = self._build_client(session)
            try:
                return client.get_subscription_status()
            finally:
                client.close()

    def pull_and_apply(self, entity_type: str, *, limit: int = 100) -> PullSummary:
        """Pull and apply one batch of changes for ``entity_type`` addressed to this device.

        Args:
            entity_type: The entity type to pull — always the same
                type a given local cursor was previously advanced
                with.
            limit: Maximum number of changes to pull in this call.

        Returns:
            How many changes were applied to this device, how many
            were addressed to a different device and skipped, and the
            cursor pulling stopped at.

        Raises:
            DeviceNotEnrolledError: This installation has not enrolled
                yet.
        """
        from sync.configuration_apply import apply_configuration_change

        with self._database.session_scope() as session:
            credential = ClientSyncCredentialRepository(session).get()
            if credential is None:
                raise DeviceNotEnrolledError(
                    "This installation has not enrolled with the Attendance Server yet; call enroll() first."
                )
            own_device_public_id = credential.device_public_id

            cursors = ClientSyncCursorRepository(session)
            since = cursors.get_cursor(entity_type)

            client = self._build_client(session)
            try:
                batch = client.pull(since, entity_type=entity_type, limit=limit)
            finally:
                client.close()

            applied = skipped = 0
            for change in batch.changes:
                if change.entity_id != own_device_public_id:
                    skipped += 1
                    continue
                apply_configuration_change(session, change)
                applied += 1

            resulting_cursor = batch.next_cursor if batch.changes else since
            if resulting_cursor != since:
                cursors.advance_cursor(entity_type, resulting_cursor)
            return PullSummary(applied=applied, skipped_other_device=skipped, next_cursor=resulting_cursor)

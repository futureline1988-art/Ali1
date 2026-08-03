"""Generic push/pull orchestration against the Developer Suite's own database.

This is the reusable engine every future synced entity (employees,
attendance, departments, licenses, settings, ...) will eventually push
and pull through — entirely generic over ``entity_type``, exactly
mirroring :mod:`server.services.sync_service`'s own "no business
domain knowledge" discipline on this side of the wire. The only
entity-specific code Phase 8 adds lives in
:mod:`developer_suite.sync.customer_sync`, registered here through
:meth:`SyncCoordinator.register_applier` rather than hard-coded — a
second entity type needs its own applier module and one
:meth:`register_applier` call, never a change to this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import httpx
from sqlalchemy.orm import Session

from database.database import Database
from developer_suite.config import DeveloperSuiteConfig
from developer_suite.repositories.sync_repository import (
    SyncCredentialRepository,
    SyncCursorRepository,
    SyncEntityVersionRepository,
    SyncOutboxRepository,
)
from developer_suite.sync.client import ChangeStatus, ChangeToPush, DeviceType, PulledChange, SyncClient, register_device

Applier = Callable[[Session, PulledChange], None]


class DeviceNotEnrolledError(Exception):
    """Raised by :meth:`SyncCoordinator.push_pending`/:meth:`~SyncCoordinator.pull_and_apply`
    before this installation has ever called :meth:`SyncCoordinator.enroll`.
    """


@dataclass(frozen=True)
class PushSummary:
    """How a :meth:`SyncCoordinator.push_pending` call's queued entries resolved."""

    applied: int = 0
    conflict: int = 0
    rejected: int = 0


@dataclass(frozen=True)
class PullSummary:
    """The outcome of one :meth:`SyncCoordinator.pull_and_apply` call."""

    applied: int = 0
    skipped_unregistered: int = 0
    next_cursor: int = 0


class SyncCoordinator:
    """Owns the outbox-push and pull-and-apply loops for one Developer Suite database.

    Constructed once (see
    :class:`~developer_suite.container.ServiceContainer`) and handed
    to any service (e.g.
    :class:`~developer_suite.services.customer_service.CustomerService`)
    that needs to enqueue outbound changes, plus to whatever drives
    :meth:`push_pending`/:meth:`pull_and_apply` — a manual "Sync now"
    action today; a future periodic background job later (the same
    ``APScheduler`` pattern :mod:`services.scheduler_service` already
    established for the Attendance Client's own device-sync/backup
    jobs) without any change to this class.
    """

    def __init__(
        self,
        database: Database,
        config: DeveloperSuiteConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Create a coordinator bound to ``database`` and this installation's configuration.

        Args:
            database: The Developer Suite's own database.
            config: This application's configuration; supplies
                :attr:`~developer_suite.config.DeveloperSuiteConfig.attendance_server_url`.
            transport: Optional ``httpx`` transport override, forwarded
                to every :class:`~developer_suite.sync.client.SyncClient`
                this coordinator builds — for tests only (see
                :mod:`developer_suite.sync.client`'s docstring).
        """
        self._database = database
        self._config = config
        self._transport = transport
        self._appliers: dict[str, Applier] = {}

    def register_applier(self, entity_type: str, applier: Applier) -> None:
        """Register the function that turns one pulled change of ``entity_type`` into a local write.

        Args:
            entity_type: The entity type this applier handles.
            applier: Called with the open session for the current pull
                batch and the pulled change. Must upsert or
                soft-delete the local row directly through that
                entity's own repository — never through its service
                layer, which would re-enqueue the very change being
                applied and loop forever.
        """
        self._appliers[entity_type] = applier

    def enroll(self, *, admin_bearer_token: str, name: str, device_type: DeviceType) -> None:
        """Register this installation with the Attendance Server and persist its credential.

        Args:
            admin_bearer_token: A token with the ``sync:admin`` scope
                (see :func:`~developer_suite.sync.client.register_device`
                for why this is required and where such a token comes
                from today).
            name: A human-readable label for this installation.
            device_type: Which application this device is — almost
                always :attr:`~developer_suite.sync.protocol.DeviceType.DEVELOPER_SUITE`
                here.
        """
        device_public_id, api_key = register_device(
            self._config.attendance_server_url,
            admin_bearer_token,
            name=name,
            device_type=device_type,
            transport=self._transport,
        )
        with self._database.session_scope() as session:
            SyncCredentialRepository(session).save(
                device_public_id=device_public_id,
                api_key=api_key,
                server_url=self._config.attendance_server_url,
            )

    def is_enrolled(self) -> bool:
        """Whether this installation has already enrolled with the Attendance Server."""
        with self._database.session_scope() as session:
            return SyncCredentialRepository(session).get() is not None

    def _build_client(self, session: Session) -> SyncClient:
        credential = SyncCredentialRepository(session).get()
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

    def push_pending(self, *, limit: int = 100) -> PushSummary:
        """Push every currently-queued outbox entry, oldest first.

        Applied changes are removed from the outbox and their entity's
        confirmed version is advanced. Conflicting or rejected changes
        stay queued with their outcome recorded (see
        :class:`~developer_suite.models.sync_state.SyncOutboxEntry`'s
        docstring) so the next local edit to that entity supersedes
        them.

        Returns:
            Counts of how each queued entry resolved. All-zero if
            nothing was queued (this installation need not be enrolled
            yet for that trivial case).

        Raises:
            DeviceNotEnrolledError: Entries are queued but this
                installation has never enrolled.
        """
        with self._database.session_scope() as session:
            outbox = SyncOutboxRepository(session)
            versions = SyncEntityVersionRepository(session)
            entries = outbox.list_pending(limit=limit)
            if not entries:
                return PushSummary()

            client = self._build_client(session)
            try:
                changes = [
                    ChangeToPush(
                        entity_type=entry.entity_type,
                        entity_id=entry.entity_id,
                        operation=entry.operation,
                        payload=entry.payload,
                        checksum=entry.checksum,
                        base_version=entry.base_version,
                    )
                    for entry in entries
                ]
                results = client.push(changes)
            finally:
                client.close()

            applied = conflict = rejected = 0
            for entry, result in zip(entries, results):
                if result.status is ChangeStatus.APPLIED:
                    versions.set_known_version(entry.entity_type, entry.entity_id, result.new_version or 0)
                    outbox.mark_pushed(entry)
                    applied += 1
                elif result.status is ChangeStatus.CONFLICT:
                    outbox.mark_conflict(entry, reason=result.conflict_reason or "Conflict.")
                    conflict += 1
                else:
                    outbox.mark_rejected(entry, reason=result.conflict_reason or "Rejected.")
                    rejected += 1
            return PushSummary(applied=applied, conflict=conflict, rejected=rejected)

    def pull_and_apply(self, entity_type: str, *, limit: int = 100) -> PullSummary:
        """Pull and apply one batch of changes for ``entity_type``.

        Changes for an ``entity_type`` with no registered applier are
        counted as skipped rather than applied — a shared server may
        carry entity types this installation does not care about — but
        the cursor still advances past them, since this installation
        has genuinely seen and deliberately ignored them.

        Args:
            entity_type: The entity type to pull — always the same
                type a given local cursor was previously advanced
                with (see
                :class:`~developer_suite.models.sync_state.SyncCursor`'s
                docstring for why mixing filters against one cursor is
                unsafe).
            limit: Maximum number of changes to pull in this call.

        Returns:
            How many changes were applied, how many were skipped, and
            the cursor pulling stopped at.

        Raises:
            DeviceNotEnrolledError: This installation has not enrolled
                yet.
        """
        with self._database.session_scope() as session:
            cursors = SyncCursorRepository(session)
            versions = SyncEntityVersionRepository(session)
            since = cursors.get_cursor(entity_type)

            client = self._build_client(session)
            try:
                batch = client.pull(since, entity_type=entity_type, limit=limit)
            finally:
                client.close()

            applier = self._appliers.get(entity_type)
            applied = skipped = 0
            for change in batch.changes:
                if applier is None:
                    skipped += 1
                    continue
                applier(session, change)
                if change.new_version is not None:
                    versions.set_known_version(change.entity_type, change.entity_id, change.new_version)
                applied += 1

            resulting_cursor = batch.next_cursor if batch.changes else since
            if resulting_cursor != since:
                cursors.advance_cursor(entity_type, resulting_cursor)
            return PullSummary(applied=applied, skipped_unregistered=skipped, next_cursor=resulting_cursor)

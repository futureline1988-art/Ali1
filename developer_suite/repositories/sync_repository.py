"""Data access for the Developer Suite's local synchronization bookkeeping.

Four small repositories, one per table in
:mod:`developer_suite.models.sync_state`, all entity-type-agnostic —
the local mirror of how :mod:`server.repositories.sync_repository`
stays generic on the server side. Every method operates against a
caller-supplied :class:`~sqlalchemy.orm.Session`, exactly like every
other Developer Suite repository (see
:mod:`developer_suite.repositories.base_repository`).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from developer_suite.models.sync_state import (
    OutboxStatus,
    SyncCursor,
    SyncDeviceCredential,
    SyncEntityVersion,
    SyncOperation,
    SyncOutboxEntry,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SyncCredentialRepository:
    """Data access for the singleton :class:`~developer_suite.models.sync_state.SyncDeviceCredential` row."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        self.session = session

    def get(self) -> SyncDeviceCredential | None:
        """Return this installation's stored credential, or ``None`` if never enrolled."""
        return self.session.execute(select(SyncDeviceCredential)).scalars().first()

    def save(self, *, device_public_id: str, api_key: str, server_url: str) -> SyncDeviceCredential:
        """Create or overwrite this installation's singleton credential row.

        Overwriting (rather than requiring the caller to delete first)
        keeps re-enrollment — e.g. after a lost credential forces
        registering a new device — a single call.

        Args:
            device_public_id: The device UUID issued at registration.
            api_key: The plaintext sync credential issued at
                registration; encrypted at rest by
                :class:`~developer_suite.models.encrypted_types.EncryptedString`.
            server_url: The Attendance Server base URL this credential
                belongs to.

        Returns:
            The saved credential row.
        """
        credential = self.get()
        if credential is None:
            credential = SyncDeviceCredential(
                device_public_id=device_public_id,
                api_key=api_key,
                server_url=server_url,
                registered_at=_utc_now(),
            )
            self.session.add(credential)
        else:
            credential.device_public_id = device_public_id
            credential.api_key = api_key
            credential.server_url = server_url
            credential.registered_at = _utc_now()
        self.session.flush()
        return credential


class SyncCursorRepository:
    """Data access for per-``entity_type`` pull cursors."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        self.session = session

    def get_cursor(self, entity_type: str) -> int:
        """Return the last pulled change id for ``entity_type`` (``0`` if never pulled)."""
        row = self.session.execute(
            select(SyncCursor).where(SyncCursor.entity_type == entity_type)
        ).scalar_one_or_none()
        return row.last_change_id if row is not None else 0

    def advance_cursor(self, entity_type: str, new_cursor: int) -> None:
        """Persist ``new_cursor`` as the last pulled change id for ``entity_type``.

        Args:
            entity_type: The synced entity type.
            new_cursor: The new cursor value; a no-op if it is not
                greater than the currently stored one, so an
                out-of-order or retried call can never move a cursor
                backwards.
        """
        row = self.session.execute(
            select(SyncCursor).where(SyncCursor.entity_type == entity_type)
        ).scalar_one_or_none()
        if row is None:
            self.session.add(SyncCursor(entity_type=entity_type, last_change_id=new_cursor))
        elif new_cursor > row.last_change_id:
            row.last_change_id = new_cursor
        self.session.flush()


class SyncEntityVersionRepository:
    """Data access for this installation's last confirmed server-side entity versions."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        self.session = session

    def get_known_version(self, entity_type: str, entity_id: str) -> int:
        """Return the last confirmed server version of one entity (``0`` if never synced)."""
        row = self._get(entity_type, entity_id)
        return row.known_version if row is not None else 0

    def set_known_version(self, entity_type: str, entity_id: str, version: int) -> None:
        """Record ``version`` as this entity's last confirmed server version."""
        row = self._get(entity_type, entity_id)
        if row is None:
            self.session.add(
                SyncEntityVersion(entity_type=entity_type, entity_id=entity_id, known_version=version)
            )
        else:
            row.known_version = version
        self.session.flush()

    def _get(self, entity_type: str, entity_id: str) -> SyncEntityVersion | None:
        return self.session.execute(
            select(SyncEntityVersion).where(
                SyncEntityVersion.entity_type == entity_type,
                SyncEntityVersion.entity_id == entity_id,
            )
        ).scalar_one_or_none()


class SyncOutboxRepository:
    """Data access for the coalesced, at-most-one-row-per-entity local change queue."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        self.session = session

    def get_by_entity(self, entity_type: str, entity_id: str) -> SyncOutboxEntry | None:
        """Return the queued outbox row for one entity, if any."""
        return self.session.execute(
            select(SyncOutboxEntry).where(
                SyncOutboxEntry.entity_type == entity_type, SyncOutboxEntry.entity_id == entity_id
            )
        ).scalar_one_or_none()

    def enqueue(
        self,
        *,
        entity_type: str,
        entity_id: str,
        operation: SyncOperation,
        payload: dict,
        checksum: str,
        base_version: int,
    ) -> SyncOutboxEntry | None:
        """Queue one local change, coalescing it with any already-pending row for this entity.

        Called once per local create/update/delete — see
        :meth:`~developer_suite.services.customer_service.CustomerService.create_customer`
        and its siblings for the call site. Behavior depends on what,
        if anything, is already queued for ``(entity_type, entity_id)``:

        * Nothing queued, or the queued row is no longer
          :attr:`~developer_suite.models.sync_state.OutboxStatus.PENDING`
          (a previous push ended in conflict or rejection): a fresh
          :attr:`~developer_suite.models.sync_state.OutboxStatus.PENDING`
          row is written with *this* call's ``base_version`` — the
          right choice either way, since a prior non-pending row's own
          ``base_version`` assumption is exactly what is now stale or
          superseded.
        * A ``PENDING`` row already exists and the new operation is not
          a ``DELETE`` following a not-yet-pushed ``CREATE``: the
          existing row's ``payload``/``checksum``/``operation`` are
          updated in place, but its original ``base_version`` is kept
          — it must keep describing the state *before* every coalesced
          edit, not just the most recent one.
        * A ``PENDING`` row already exists with
          :attr:`~developer_suite.models.sync_state.SyncOperation.CREATE`
          and the new operation is
          :attr:`~developer_suite.models.sync_state.SyncOperation.DELETE`:
          the entity was created and deleted locally before the server
          ever learned about it, so the queued row is removed entirely
          rather than pushed — there is nothing for the server to
          apply.

        Returns:
            The resulting queued row, or ``None`` when a not-yet-pushed
            create/delete pair canceled each other out.
        """
        existing = self.get_by_entity(entity_type, entity_id)

        if existing is None or existing.status is not OutboxStatus.PENDING:
            if existing is not None:
                self.session.delete(existing)
                self.session.flush()
            entry = SyncOutboxEntry(
                entity_type=entity_type,
                entity_id=entity_id,
                operation=operation,
                payload=payload,
                checksum=checksum,
                base_version=base_version,
                status=OutboxStatus.PENDING,
            )
            self.session.add(entry)
            self.session.flush()
            return entry

        if existing.operation is SyncOperation.CREATE and operation is SyncOperation.DELETE:
            self.session.delete(existing)
            self.session.flush()
            return None

        if existing.operation is SyncOperation.CREATE:
            new_operation = SyncOperation.CREATE
        elif operation is SyncOperation.DELETE:
            new_operation = SyncOperation.DELETE
        else:
            new_operation = SyncOperation.UPDATE

        existing.operation = new_operation
        existing.payload = payload
        existing.checksum = checksum
        self.session.flush()
        return existing

    def list_pending(self, *, limit: int = 100) -> list[SyncOutboxEntry]:
        """List queued rows still awaiting a push attempt, oldest first."""
        statement = (
            select(SyncOutboxEntry)
            .where(SyncOutboxEntry.status == OutboxStatus.PENDING)
            .order_by(SyncOutboxEntry.id)
            .limit(limit)
        )
        return list(self.session.execute(statement).scalars().all())

    def mark_pushed(self, entry: SyncOutboxEntry) -> None:
        """Remove a successfully applied entry — nothing is left to queue for it."""
        self.session.delete(entry)
        self.session.flush()

    def mark_conflict(self, entry: SyncOutboxEntry, *, reason: str) -> None:
        """Record that a push attempt for ``entry`` came back in conflict."""
        entry.status = OutboxStatus.CONFLICT
        entry.conflict_reason = reason
        self.session.flush()

    def mark_rejected(self, entry: SyncOutboxEntry, *, reason: str) -> None:
        """Record that a push attempt for ``entry`` was rejected."""
        entry.status = OutboxStatus.REJECTED
        entry.conflict_reason = reason
        self.session.flush()

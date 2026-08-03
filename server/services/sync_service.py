"""The synchronization protocol: push, pull, and conflict resolution.

This is the reusable engine every future business domain (customers,
licenses, remote configuration, ...) will eventually push and pull
through — entirely generic over ``entity_type``/``entity_id``, exactly
as :class:`~server.models.sync.ChangeRecord`'s own docstring describes.
No business domain is wired into it yet.

Protocol, in one place:

1. **Push** (:meth:`SyncService.push_changes`): a device submits a
   batch of local changes. The whole batch runs in one transaction
   that first calls :meth:`~server.repositories.sync_repository.SyncRepository.acquire_sequence_lock`
   — see :class:`~server.models.sync.SyncSequence`'s docstring for
   exactly what that buys: no concurrent transaction anywhere on the
   server can insert an :attr:`~server.models.sync.ChangeStatus.APPLIED`
   row while this one is in flight, which is what makes
   :meth:`pull_changes`'s cursor gap-free. For each change, in order:

   a. The checksum is recomputed server-side and compared —
      :attr:`~server.models.sync.ChangeStatus.REJECTED` immediately on
      mismatch (data integrity verification), before anything else
      runs.
   b. The entity's current version is looked up (0 if never seen
      before). If it does not equal ``base_version``, the change is
      recorded as :attr:`~server.models.sync.ChangeStatus.CONFLICT`
      rather than blindly overwritten — this, not last-write-wins, is
      what makes offline-first safe: a device that was disconnected
      for days and pushes a stale change gets a conflict to reconcile,
      never a silent lost update.
   c. Otherwise the change is
      :attr:`~server.models.sync.ChangeStatus.APPLIED`: the entity's
      version is bumped by one and a new, immutable
      :class:`~server.models.sync.ChangeRecord` row is appended to the
      log.

2. **Pull** (:meth:`SyncService.pull_changes`): a device asks for every
   applied change after a cursor (an opaque integer — the highest
   :class:`~server.models.sync.ChangeRecord` id it has already seen). A
   device that was offline for any length of time simply resumes from
   its last cursor; there is no session or subscription to have
   expired while it was gone. Because every applied insert is
   serialized through the sequence lock (see above), "everything with
   id greater than N" can never silently skip a row, no matter how the
   underlying PostgreSQL transactions happened to interleave.

3. **Conflict resolution** (:meth:`SyncService.resolve_conflict`): a
   human or a future admin tool decides, per conflicting change,
   whether to force-apply it or discard it. Nothing here picks a
   winner automatically — that policy is inherently domain-specific
   and stays out of scope for a foundation phase. Force-applying
   *appends a new* :class:`~server.models.sync.ChangeRecord` rather
   than flipping the original conflicting row's status in place —
   necessary for the same reason push results are never mutated after
   the fact: a row that only becomes ``APPLIED`` well after its id was
   assigned would be invisible to any client that already pulled past
   that id, exactly the gap this whole design exists to prevent.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from server.models.sync import ChangeRecord, ChangeStatus, EntityVersion, SyncOperation
from server.repositories.sync_repository import SyncRepository
from server.services.base_service import BaseService


class SyncServiceError(Exception):
    """Base class for sync operation failures the API layer should translate."""


class ChangeRecordNotFoundError(SyncServiceError):
    """No change record exists with the given id."""


class ChangeNotInConflictError(SyncServiceError):
    """:meth:`SyncService.resolve_conflict` was called on a change that is not in conflict."""


@dataclass(frozen=True)
class ChangeInput:
    """One change a device is pushing.

    Attributes:
        entity_type: A stable string identifying what kind of entity
            this is.
        entity_id: The entity's public UUID, as a string.
        operation: What kind of change this is.
        payload: The change's JSON-safe data.
        checksum: SHA-256 hex digest the device computed over
            ``payload`` via :meth:`SyncService.compute_checksum` — must
            match what the server recomputes, or the change is
            rejected.
        base_version: The version the device believes this entity is
            currently at; ``0`` (or omit) for a first-ever create.
    """

    entity_type: str
    entity_id: str
    operation: SyncOperation
    payload: dict
    checksum: str
    base_version: int = 0


@dataclass(frozen=True)
class PushResult:
    """The outcome of processing one :class:`ChangeInput`.

    Attributes:
        entity_type: Echoes the input, so callers can match results
            back to requests positionally or by key.
        entity_id: Echoes the input.
        status: The outcome.
        new_version: The entity's resulting version; ``None`` unless
            :attr:`status` is :attr:`~server.models.sync.ChangeStatus.APPLIED`.
        conflict_reason: Set when :attr:`status` is
            :attr:`~server.models.sync.ChangeStatus.CONFLICT` or
            :attr:`~server.models.sync.ChangeStatus.REJECTED`.
        change_record_id: The persisted
            :class:`~server.models.sync.ChangeRecord`'s id, for later
            lookup (e.g. via :meth:`SyncService.resolve_conflict`).
    """

    entity_type: str
    entity_id: str
    status: ChangeStatus
    new_version: int | None
    conflict_reason: str | None
    change_record_id: int


class SyncService(BaseService):
    """Push, pull, and resolve conflicts against the generic change ledger."""

    @staticmethod
    def compute_checksum(payload: dict) -> str:
        """Compute the canonical SHA-256 checksum of a change payload.

        Args:
            payload: The JSON-safe payload to checksum.

        Returns:
            A hex-encoded SHA-256 digest of ``payload``'s canonical
            (sorted-key, separator-normalized) JSON encoding — the
            exact same encoding a pushing device must use for its
            checksum to ever match.
        """
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def push_changes(self, device_id: int, changes: list[ChangeInput]) -> list[PushResult]:
        """Process a batch of pushed changes, applying, conflicting, or rejecting each.

        Args:
            device_id: The authenticated device pushing these changes
                (see :meth:`~server.services.device_service.DeviceService.authenticate_device`).
            changes: The batch to process, applied in order.

        Returns:
            One :class:`PushResult` per input change, in the same order.
        """
        results: list[PushResult] = []
        with self._session_scope() as session:
            repo = SyncRepository(session)
            repo.acquire_sequence_lock()
            for change in changes:
                results.append(self._apply_one_change(repo, device_id, change))
        return results

    def _apply_one_change(
        self, repo: SyncRepository, device_id: int, change: ChangeInput
    ) -> PushResult:
        """Apply, conflict, or reject a single change within an already-open session."""
        expected_checksum = self.compute_checksum(change.payload)
        if change.checksum != expected_checksum:
            record = ChangeRecord(
                device_id=device_id,
                entity_type=change.entity_type,
                entity_id=change.entity_id,
                operation=change.operation,
                payload=change.payload,
                checksum=change.checksum,
                base_version=change.base_version,
                new_version=None,
                status=ChangeStatus.REJECTED,
                conflict_reason="Checksum mismatch: payload failed integrity verification.",
            )
            repo.change_records.add(record)
            return PushResult(
                entity_type=change.entity_type,
                entity_id=change.entity_id,
                status=ChangeStatus.REJECTED,
                new_version=None,
                conflict_reason=record.conflict_reason,
                change_record_id=record.id,
            )

        version_row = repo.get_entity_version(change.entity_type, change.entity_id)
        current_version = version_row.current_version if version_row is not None else 0

        if change.base_version != current_version:
            conflict_reason = (
                f"Expected base version {change.base_version}, entity is at {current_version}."
            )
            record = ChangeRecord(
                device_id=device_id,
                entity_type=change.entity_type,
                entity_id=change.entity_id,
                operation=change.operation,
                payload=change.payload,
                checksum=change.checksum,
                base_version=change.base_version,
                new_version=None,
                status=ChangeStatus.CONFLICT,
                conflict_reason=conflict_reason,
            )
            repo.change_records.add(record)
            return PushResult(
                entity_type=change.entity_type,
                entity_id=change.entity_id,
                status=ChangeStatus.CONFLICT,
                new_version=None,
                conflict_reason=conflict_reason,
                change_record_id=record.id,
            )

        new_version = current_version + 1
        record = ChangeRecord(
            device_id=device_id,
            entity_type=change.entity_type,
            entity_id=change.entity_id,
            operation=change.operation,
            payload=change.payload,
            checksum=change.checksum,
            base_version=change.base_version,
            new_version=new_version,
            status=ChangeStatus.APPLIED,
        )
        repo.change_records.add(record)
        if version_row is None:
            repo.entity_versions.add(
                EntityVersion(
                    entity_type=change.entity_type,
                    entity_id=change.entity_id,
                    current_version=new_version,
                )
            )
        else:
            version_row.current_version = new_version
        return PushResult(
            entity_type=change.entity_type,
            entity_id=change.entity_id,
            status=ChangeStatus.APPLIED,
            new_version=new_version,
            conflict_reason=None,
            change_record_id=record.id,
        )

    def pull_changes(
        self, since_id: int, *, entity_type: str | None = None, limit: int = 100
    ) -> tuple[list[ChangeRecord], int]:
        """Fetch one batch of applied changes after a cursor.

        Args:
            since_id: Resume after this change id (``0`` for the very
                beginning).
            entity_type: Optionally restrict to one entity type.
            limit: Maximum number of changes to return.

        Returns:
            ``(changes, next_cursor)`` — ``next_cursor`` is the id of
            the last change returned, ready to pass back in as
            ``since_id`` on the next call; equal to ``since_id`` itself
            when there is nothing new to pull.
        """
        with self._session_scope() as session:
            changes = SyncRepository(session).list_changes_since(
                since_id, entity_type=entity_type, limit=limit
            )
            next_cursor = changes[-1].id if changes else since_id
            return changes, next_cursor

    def list_conflicts(self, *, device_id: int | None = None) -> list[ChangeRecord]:
        """List unresolved conflicts, optionally for one device.

        Args:
            device_id: Optionally restrict to changes pushed by one
                device.

        Returns:
            Every unresolved
            :attr:`~server.models.sync.ChangeStatus.CONFLICT` change.
        """
        with self._session_scope() as session:
            return SyncRepository(session).list_conflicts(device_id=device_id)

    def list_recent_activity(self, *, limit: int = 50) -> list[ChangeRecord]:
        """List the most recent change records of any status, most recent first.

        A read-only administrative view over the same append-only
        ledger :meth:`push_changes`/:meth:`pull_changes` already read
        and write — no new data, no new write path, purely a
        different read shape for a monitoring dashboard (see
        :mod:`developer_suite.admin.client`) than :meth:`pull_changes`
        (which only ever returns :attr:`~server.models.sync.ChangeStatus.APPLIED`
        rows, filtered by cursor) or :meth:`list_conflicts` (only
        :attr:`~server.models.sync.ChangeStatus.CONFLICT` rows) provide.

        Args:
            limit: Maximum number of change records to return.

        Returns:
            Up to ``limit`` change records of any status, most recent
            first.
        """
        with self._session_scope() as session:
            return SyncRepository(session).list_recent(limit=limit)

    def resolve_conflict(self, change_id: int, *, apply_incoming: bool) -> ChangeRecord:
        """Resolve a conflicting change by force-applying or discarding it.

        Args:
            change_id: The conflicting :class:`~server.models.sync.ChangeRecord` to resolve.
            apply_incoming: If ``True``, force-applies the change by
                appending a *new*
                :class:`~server.models.sync.ChangeRecord` with
                :attr:`~server.models.sync.ChangeStatus.APPLIED`
                (bumping the entity's version regardless of the
                earlier mismatch) — the original conflicting row is
                left in place, its status changed to
                :attr:`~server.models.sync.ChangeStatus.REJECTED`
                ("superseded"), for history. If ``False``, the original
                row itself is marked
                :attr:`~server.models.sync.ChangeStatus.REJECTED` and
                discarded; nothing new is appended.

        Returns:
            The change record a puller will actually see: the newly
            appended row when ``apply_incoming`` is ``True``, or the
            (now-rejected) original row when it is ``False``.

        Raises:
            ChangeRecordNotFoundError: No change exists with that id.
            ChangeNotInConflictError: The change's current status is
                not :attr:`~server.models.sync.ChangeStatus.CONFLICT`.
        """
        with self._session_scope() as session:
            repo = SyncRepository(session)
            record = repo.get_change_record(change_id)
            if record is None:
                raise ChangeRecordNotFoundError(f"No change record with id={change_id!r}.")
            if record.status is not ChangeStatus.CONFLICT:
                raise ChangeNotInConflictError(
                    f"Change {change_id!r} is not in conflict (status={record.status.value!r})."
                )

            if not apply_incoming:
                record.status = ChangeStatus.REJECTED
                record.conflict_reason = f"{record.conflict_reason} Resolved: discarded."
                session.flush()
                return record

            # Force-apply: never flip this row's status to APPLIED in
            # place - its id was assigned back when it was first
            # pushed, long before this call, so a puller that already
            # passed that id would never see the transition. Instead,
            # append a brand-new row through the same locked path
            # push_changes uses, so it lands at whatever id is
            # currently next - always ahead of every cursor that could
            # possibly exist right now. See SyncSequence's docstring.
            repo.acquire_sequence_lock()
            version_row = repo.get_entity_version(record.entity_type, record.entity_id)
            current_version = version_row.current_version if version_row is not None else 0
            new_version = current_version + 1

            resolved_record = ChangeRecord(
                device_id=record.device_id,
                entity_type=record.entity_type,
                entity_id=record.entity_id,
                operation=record.operation,
                payload=record.payload,
                checksum=record.checksum,
                base_version=record.base_version,
                new_version=new_version,
                status=ChangeStatus.APPLIED,
                conflict_reason=f"Resolved from conflicting change #{record.id}: force-applied.",
            )
            repo.change_records.add(resolved_record)
            if version_row is None:
                repo.entity_versions.add(
                    EntityVersion(
                        entity_type=record.entity_type,
                        entity_id=record.entity_id,
                        current_version=new_version,
                    )
                )
            else:
                version_row.current_version = new_version

            record.status = ChangeStatus.REJECTED
            record.conflict_reason = (
                f"{record.conflict_reason} Resolved: superseded by change #{resolved_record.id}."
            )
            session.flush()
            return resolved_record

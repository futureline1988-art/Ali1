"""Tests for Phase 7.1: synchronization protocol hardening.

**A note on what this file can and cannot prove.** The bug this
hardening pass fixes is a PostgreSQL-specific MVCC visibility race: a
transaction that is assigned a lower :class:`~server.models.sync.ChangeRecord`
id can still commit *after* a transaction that was assigned a higher
one, and a client pulling at exactly the wrong moment would advance its
cursor past the higher id and then permanently miss the lower one once
it finally commits. SQLite cannot reproduce this race, structurally,
regardless of anything under test here: it serializes every writer at
the whole-database level (one write transaction at a time, full stop),
so id-assignment order and commit order are trivially identical on
SQLite whether or not :meth:`~server.repositories.sync_repository.SyncRepository.acquire_sequence_lock`
does anything at all. A test that tries to "reproduce the race and show
the fix prevents it" is therefore impossible to write honestly against
SQLite — there is no SQLite scenario where the race could occur in the
first place, fixed or not.

What *is* deterministically testable here, and covered below:

* The append-only conflict-resolution fix
  (:class:`TestConflictResolutionIsAppendOnly`) — this is not a
  concurrency scenario at all, just sequential logic, and it is the
  other half of "no committed change can be skipped": before this
  pass, force-resolving a conflict flipped an *existing* row's status
  in place, which a client that already pulled past that row's id
  would never see. This is fully reproducible on SQLite and is the
  highest-value test in this file.
* The locking mechanism itself functions correctly and is dialect-safe
  (:class:`TestSequenceLock`).
* The schema changes this pass makes are actually present
  (:class:`TestIndexes`).
* A best-effort concurrent-push stress test
  (:class:`TestConcurrentPushIntegrity`) — proves general data
  integrity (no double-applied change, no corrupted version ledger)
  under real multi-threaded contention against a shared SQLite file.
  This is a valuable safety net, but it is not, and cannot be, a test
  of the specific PostgreSQL commit-order race described above.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import server.config as server_config_module
from sqlalchemy import inspect, select
from server.config import ServerConfig, get_server_config
from server.database.bootstrap import build_database
from server.models.device import DeviceType
from server.models.sync import ChangeRecord, ChangeStatus, EntityVersion, SyncSequence
from server.repositories.sync_repository import SyncRepository
from server.services.device_service import DeviceService
from server.services.sync_service import ChangeInput, SyncOperation, SyncService


def _checksum(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@pytest.fixture
def server_config(tmp_path, monkeypatch) -> ServerConfig:
    monkeypatch.setenv("ATTENDANCE_SERVER_DB_SQLITE_PATH", str(tmp_path / "attendance_server_test.db"))
    monkeypatch.setenv("ATTENDANCE_SERVER_SECRET_KEY", "test-secret-key")
    server_config_module._config_instance = None
    yield get_server_config()
    server_config_module._config_instance = None


@pytest.fixture
def server_database(server_config: ServerConfig):
    database = build_database(server_config)
    yield database
    database.dispose()


@pytest.fixture
def device_service(server_database, server_config: ServerConfig) -> DeviceService:
    return DeviceService(server_database, config=server_config)


@pytest.fixture
def sync_service(server_database) -> SyncService:
    return SyncService(server_database)


@pytest.fixture
def registered_device(device_service: DeviceService):
    return device_service.register_device(name="Hardening Test Device", device_type=DeviceType.ATTENDANCE_CLIENT)


class TestSyncSequenceBootstrap:
    def test_seeds_exactly_one_row(self, server_database) -> None:
        with server_database.session_scope() as session:
            rows = session.execute(select(SyncSequence)).scalars().all()
        assert len(rows) == 1

    def test_seeding_is_idempotent_across_repeated_bootstrap(self, server_config: ServerConfig) -> None:
        # build_database() runs the seed-if-missing step every call;
        # calling it again against the same already-provisioned
        # database must not create a second row.
        second_handle = build_database(server_config)
        try:
            with second_handle.session_scope() as session:
                rows = session.execute(select(SyncSequence)).scalars().all()
            assert len(rows) == 1
        finally:
            second_handle.dispose()


class TestSequenceLock:
    def test_acquire_sequence_lock_succeeds_and_is_a_noop_on_the_row(self, server_database) -> None:
        with server_database.session_scope() as session:
            SyncRepository(session).acquire_sequence_lock()
        # No exception, and the row still exists afterward, unmodified.
        with server_database.session_scope() as session:
            rows = session.execute(select(SyncSequence)).scalars().all()
        assert len(rows) == 1

    def test_lock_can_be_acquired_again_in_a_later_transaction(self, server_database) -> None:
        # Proves the lock is released on commit, not held forever.
        with server_database.session_scope() as session:
            SyncRepository(session).acquire_sequence_lock()
        with server_database.session_scope() as session:
            SyncRepository(session).acquire_sequence_lock()

    def test_push_changes_acquires_the_lock_without_error(
        self, sync_service: SyncService, registered_device
    ) -> None:
        device, _api_key = registered_device
        payload = {"n": 1}
        results = sync_service.push_changes(
            device.id,
            [
                ChangeInput(
                    entity_type="widget",
                    entity_id="w-1",
                    operation=SyncOperation.CREATE,
                    payload=payload,
                    checksum=_checksum(payload),
                    base_version=0,
                )
            ],
        )
        assert results[0].status is ChangeStatus.APPLIED


class TestIndexes:
    def test_change_records_has_composite_status_id_index(self) -> None:
        index_names = {index.name for index in ChangeRecord.__table__.indexes}
        assert "ix_change_records_status_id" in index_names
        composite = next(
            index for index in ChangeRecord.__table__.indexes if index.name == "ix_change_records_status_id"
        )
        assert [column.name for column in composite.columns] == ["status", "id"]

    def test_change_records_status_column_has_no_redundant_standalone_index(self) -> None:
        single_column_status_indexes = [
            index
            for index in ChangeRecord.__table__.indexes
            if [c.name for c in index.columns] == ["status"]
        ]
        assert single_column_status_indexes == []

    def test_entity_version_has_no_redundant_standalone_indexes(self) -> None:
        # Only the composite unique-constraint index should exist -
        # no separate single-column index on entity_type or entity_id.
        index_column_sets = [
            tuple(column.name for column in index.columns) for index in EntityVersion.__table__.indexes
        ]
        assert ("entity_type",) not in index_column_sets
        assert ("entity_id",) not in index_column_sets

    def test_entity_version_composite_unique_constraint_still_covers_the_lookup(self) -> None:
        constraint_names = {
            constraint.name
            for constraint in EntityVersion.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        assert "uq_entity_versions_entity_type_entity_id" in constraint_names

    def test_indexes_exist_in_the_actual_created_schema(self, server_database) -> None:
        # Not just declared in Python - actually present in the database
        # build_database() created.
        inspector = inspect(server_database.engine)
        index_names = {index["name"] for index in inspector.get_indexes("change_records")}
        assert "ix_change_records_status_id" in index_names


class TestConflictResolutionIsAppendOnly:
    """The fully deterministic half of "no committed change can be skipped".

    Before this pass, force-resolving a conflict mutated the existing
    CONFLICT row's status to APPLIED in place. A client that had
    already pulled past that row's id would never see it change -
    exactly the same class of silent, permanent miss the sequence lock
    fixes for concurrent inserts, just triggered by a status
    transition instead of a commit race, and (unlike that race) fully
    reproducible without any concurrency at all.
    """

    def _create_conflict(self, sync_service: SyncService, device) -> tuple[int, int]:
        """Push a change, then push a stale duplicate to produce a conflict.

        Returns:
            ``(first_change_id, conflicting_change_id)``.
        """
        payload = {"n": 1}
        first_results = sync_service.push_changes(
            device.id,
            [
                ChangeInput(
                    entity_type="widget",
                    entity_id="w-1",
                    operation=SyncOperation.CREATE,
                    payload=payload,
                    checksum=_checksum(payload),
                    base_version=0,
                )
            ],
        )
        conflict_results = sync_service.push_changes(
            device.id,
            [
                ChangeInput(
                    entity_type="widget",
                    entity_id="w-1",
                    operation=SyncOperation.UPDATE,
                    payload=payload,
                    checksum=_checksum(payload),
                    base_version=0,
                )
            ],
        )
        return first_results[0].change_record_id, conflict_results[0].change_record_id

    def test_force_apply_appends_a_new_row_instead_of_mutating_the_conflict_row(
        self, sync_service: SyncService, registered_device
    ) -> None:
        device, _api_key = registered_device
        _first_id, conflict_id = self._create_conflict(sync_service, device)

        resolved = sync_service.resolve_conflict(conflict_id, apply_incoming=True)

        assert resolved.id != conflict_id
        assert resolved.status is ChangeStatus.APPLIED

        with sync_service.database.session_scope() as session:
            original = SyncRepository(session).get_change_record(conflict_id)
        assert original.status is ChangeStatus.REJECTED
        assert "superseded" in original.conflict_reason.lower()

    def test_client_that_already_pulled_past_the_conflict_id_still_sees_the_resolution(
        self, sync_service: SyncService, registered_device
    ) -> None:
        """The core regression test: simulates the exact gap scenario deterministically.

        A conflict is created, then a *later, unrelated* change is
        pushed and applied (an ordinary event in a busy log - some
        other entity's change lands after this conflict while it sits
        unresolved). A client pulls both, advancing its cursor past
        the conflict's id even though the conflict itself was never
        returned. The conflict is only resolved *after* that. Without
        this pass's fix, resolving it would flip the existing
        (already-passed) row's status in place, and the client's next
        pull (`id > cursor`) would never see it again - a silent,
        permanent miss of a legitimately committed change. With the
        fix, resolving appends a fresh row, positioned after the
        client's cursor, so the next pull correctly picks it up.
        """
        device, _api_key = registered_device
        _first_id, conflict_id = self._create_conflict(sync_service, device)

        # An unrelated change lands after the conflict while it's still open.
        other_payload = {"n": 2}
        sync_service.push_changes(
            device.id,
            [
                ChangeInput(
                    entity_type="widget",
                    entity_id="w-2",
                    operation=SyncOperation.CREATE,
                    payload=other_payload,
                    checksum=_checksum(other_payload),
                    base_version=0,
                )
            ],
        )

        # Client pulls everything currently visible and advances its cursor
        # past the conflict's id, even though the conflict itself was
        # never returned.
        changes, cursor = sync_service.pull_changes(0)
        assert conflict_id not in [c.id for c in changes]  # conflicts are never pulled
        assert cursor > conflict_id  # the client's cursor is now past the conflict's id

        # The conflict resolves only now, after the client's cursor already moved past it.
        resolved = sync_service.resolve_conflict(conflict_id, apply_incoming=True)

        # Resuming from its already-advanced cursor, the client must still see it.
        next_changes, _next_cursor = sync_service.pull_changes(cursor)
        assert [c.id for c in next_changes] == [resolved.id]

    def test_discard_does_not_append_a_new_row(
        self, sync_service: SyncService, registered_device
    ) -> None:
        device, _api_key = registered_device
        _first_id, conflict_id = self._create_conflict(sync_service, device)

        before_changes, _ = sync_service.pull_changes(0)
        resolved = sync_service.resolve_conflict(conflict_id, apply_incoming=False)
        after_changes, _ = sync_service.pull_changes(0)

        assert resolved.id == conflict_id
        assert resolved.status is ChangeStatus.REJECTED
        assert len(after_changes) == len(before_changes)

    def test_entity_version_reflects_the_force_applied_resolution_only_once(
        self, sync_service: SyncService, registered_device
    ) -> None:
        device, _api_key = registered_device
        _first_id, conflict_id = self._create_conflict(sync_service, device)
        sync_service.resolve_conflict(conflict_id, apply_incoming=True)

        with sync_service.database.session_scope() as session:
            version_row = SyncRepository(session).get_entity_version("widget", "w-1")
        assert version_row.current_version == 2


class TestConcurrentPushIntegrity:
    """Best-effort thread-level stress test — see module docstring for scope.

    Proves the push path stays correct (no double-applied change for
    the same new entity, no corrupted version ledger) under genuine
    concurrent access from two threads sharing one SQLite file. This
    does not, and cannot, reproduce the PostgreSQL commit-order race
    the rest of this pass exists to fix; it is a general concurrency
    safety net, not a regression test for that specific bug.
    """

    def test_two_concurrent_pushes_for_the_same_new_entity_never_both_apply(
        self, server_config: ServerConfig, device_service: DeviceService, server_database
    ) -> None:
        device, _api_key = device_service.register_device(
            name="Concurrent Device", device_type=DeviceType.ATTENDANCE_CLIENT
        )
        sync_service = SyncService(server_database)
        payload = {"n": 1}
        change = ChangeInput(
            entity_type="widget",
            entity_id="race-1",
            operation=SyncOperation.CREATE,
            payload=payload,
            checksum=_checksum(payload),
            base_version=0,
        )

        outcomes: list[ChangeStatus] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def _push() -> None:
            try:
                results = sync_service.push_changes(device.id, [change])
                with lock:
                    outcomes.append(results[0].status)
            except Exception as exc:  # SQLite may raise "database is locked" under contention
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=_push) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        applied_count = outcomes.count(ChangeStatus.APPLIED)
        # Exactly one of the two attempts may succeed as APPLIED for a
        # brand-new entity - never both. The other either conflicts or
        # (on SQLite, under lock contention) raises, which is also an
        # acceptable, safe outcome - what matters is it is never silently
        # double-applied.
        assert applied_count <= 1

        with server_database.session_scope() as session:
            version_row = SyncRepository(session).get_entity_version("widget", "race-1")
        if version_row is not None:
            assert version_row.current_version == 1

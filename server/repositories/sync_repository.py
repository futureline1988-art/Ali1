"""Data access for the generic synchronization ledger.

Composes :class:`~server.repositories.base_repository.BaseRepository`
for :class:`~server.models.sync.ChangeRecord` and
:class:`~server.models.sync.EntityVersion`, plus the handful of custom
queries the sync protocol actually needs — the same "one repository
class per feature area, generic CRUD by composition" shape
:mod:`developer_suite.repositories.configuration_repository` already
established.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from server.models.sync import ChangeRecord, ChangeStatus, EntityVersion, SyncSequence
from server.repositories.base_repository import BaseRepository


class SyncRepository:
    """Data access for the change-tracking ledger, bound to one session.

    Attributes:
        change_records: CRUD for :class:`~server.models.sync.ChangeRecord`.
        entity_versions: CRUD for :class:`~server.models.sync.EntityVersion`.
    """

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        self.session = session
        self.change_records = BaseRepository[ChangeRecord](session, model=ChangeRecord)
        self.entity_versions = BaseRepository[EntityVersion](session, model=EntityVersion)

    def acquire_sequence_lock(self) -> None:
        """Block until this transaction exclusively holds the sync-sequence lock.

        Must be called exactly once, before any
        :class:`~server.models.sync.ChangeRecord` insert, in every
        transaction that writes an
        :attr:`~server.models.sync.ChangeStatus.APPLIED` change (see
        :class:`~server.models.sync.SyncSequence`'s docstring for the
        full argument for why this makes the pull cursor gap-free).

        Under PostgreSQL this emits ``SELECT ... FOR UPDATE`` and
        genuinely blocks a concurrent transaction until this one
        commits or rolls back. Under SQLite, ``FOR UPDATE`` has no
        dialect support and SQLAlchemy silently omits it — harmless,
        since SQLite already serializes every writer at the database
        level regardless (see
        :mod:`tests.test_server_phase7_1_hardening`'s module
        docstring), so this call is a correct no-op there rather than
        a broken one.
        """
        self.session.execute(select(SyncSequence).with_for_update()).scalar_one()

    def get_change_record(self, change_id: int) -> ChangeRecord | None:
        """Fetch a single change record by id, with its device eagerly loaded.

        Args:
            change_id: The change record's ``id``.

        Returns:
            The matching change record, or ``None`` if not found.
        """
        statement = (
            select(ChangeRecord)
            .options(joinedload(ChangeRecord.device))
            .where(ChangeRecord.id == change_id, ChangeRecord.is_deleted.is_(False))
        )
        return self.session.execute(statement).scalar_one_or_none()

    def get_entity_version(self, entity_type: str, entity_id: str) -> EntityVersion | None:
        """Fetch the current version ledger row for one entity, if it exists.

        Args:
            entity_type: The entity's type string.
            entity_id: The entity's public UUID, as a string.

        Returns:
            The matching :class:`~server.models.sync.EntityVersion`, or
            ``None`` if this entity has never had a change applied.
        """
        statement = select(EntityVersion).where(
            EntityVersion.entity_type == entity_type,
            EntityVersion.entity_id == entity_id,
            EntityVersion.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

    def list_changes_since(
        self,
        since_id: int,
        *,
        entity_type: str | None = None,
        limit: int = 100,
    ) -> list[ChangeRecord]:
        """List applied changes after a cursor, oldest first, for :meth:`~server.services.sync_service.SyncService.pull_changes`.

        Args:
            since_id: Return only changes with ``id`` greater than this
                cursor (``0`` to start from the very beginning).
            entity_type: Optionally restrict to one entity type.
            limit: Maximum number of changes to return (one batch).

        Returns:
            Up to ``limit`` :attr:`~server.models.sync.ChangeStatus.APPLIED`
            changes, ordered by ``id`` ascending, with each row's device
            eagerly loaded.
        """
        statement = (
            select(ChangeRecord)
            .options(joinedload(ChangeRecord.device))
            .where(
                ChangeRecord.id > since_id,
                ChangeRecord.status == ChangeStatus.APPLIED,
                ChangeRecord.is_deleted.is_(False),
            )
            .order_by(ChangeRecord.id)
            .limit(limit)
        )
        if entity_type is not None:
            statement = statement.where(ChangeRecord.entity_type == entity_type)
        return list(self.session.execute(statement).unique().scalars().all())

    def list_conflicts(self, *, device_id: int | None = None) -> list[ChangeRecord]:
        """List unresolved conflicts, most recent first.

        Args:
            device_id: Optionally restrict to changes pushed by one
                device.

        Returns:
            Every :attr:`~server.models.sync.ChangeStatus.CONFLICT`
            change record, with each row's device eagerly loaded.
        """
        statement = (
            select(ChangeRecord)
            .options(joinedload(ChangeRecord.device))
            .where(ChangeRecord.status == ChangeStatus.CONFLICT, ChangeRecord.is_deleted.is_(False))
            .order_by(ChangeRecord.id.desc())
        )
        if device_id is not None:
            statement = statement.where(ChangeRecord.device_id == device_id)
        return list(self.session.execute(statement).unique().scalars().all())

    def list_recent(self, *, limit: int = 50) -> list[ChangeRecord]:
        """List the most recent change records of any status, most recent first.

        The generic read behind an administrative "recent activity"
        view (see :meth:`~server.services.sync_service.SyncService.list_recent_activity`) —
        deliberately unfiltered by status, so a caller can derive
        "latest activity," "recent errors" (``REJECTED``), or a
        synchronization-failure view (``CONFLICT``/``REJECTED``) from
        one query instead of this repository growing one near
        -duplicate method per view.

        Args:
            limit: Maximum number of change records to return.

        Returns:
            Up to ``limit`` change records, most recent first, with
            each row's device eagerly loaded.
        """
        statement = (
            select(ChangeRecord)
            .options(joinedload(ChangeRecord.device))
            .where(ChangeRecord.is_deleted.is_(False))
            .order_by(ChangeRecord.id.desc())
            .limit(limit)
        )
        return list(self.session.execute(statement).unique().scalars().all())

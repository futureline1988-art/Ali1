"""The generic change-tracking ledger that powers synchronization.

Three tables, all entity-type-agnostic — none of them know anything
about customers, licenses, or configuration, the same way
:mod:`developer_suite.repositories.base_repository` knows nothing
about any specific model:

* :class:`EntityVersion` — one row per synced entity (identified by
  ``(entity_type, entity_id)``), holding only its current version
  number. The O(1) lookup :meth:`~server.services.sync_service.SyncService.push_changes`
  checks a proposed change against for conflict detection, so a push
  never needs to scan :class:`ChangeRecord` history to find "what
  version is this entity at right now."
* :class:`ChangeRecord` — one append-only row per accepted, rejected,
  or conflicting change, forming the durable log
  :meth:`~server.services.sync_service.SyncService.pull_changes` reads
  from. Genuinely append-only as of Phase 7.1: an
  :attr:`~ChangeStatus.APPLIED` row is *never* produced by mutating an
  existing row after the fact — see :class:`SyncSequence`'s docstring
  and :meth:`~server.services.sync_service.SyncService.resolve_conflict`
  for why that distinction is exactly what makes the pull cursor safe.
* :class:`SyncSequence` — a single lock row with no business meaning of
  its own, existing purely so every writer of :class:`ChangeRecord`
  can serialize against it (see its own docstring for the full
  argument).

``entity_id`` is deliberately a string holding the origin entity's
*public* UUID (e.g. a future ``Customer.public_id``), never its local
integer primary key — the same id space already used for cross-process
references everywhere else in this codebase (see
``models.base.UUIDMixin``'s docstring), and the only kind of id that
stays meaningful once many independent per-company databases are all
talking to one shared server.
"""

from __future__ import annotations

from enum import Enum

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import enum_column_type
from server.database.base import Base, ServerBaseModel
from server.models.device import SyncDevice


class SyncOperation(str, Enum):
    """What kind of change a :class:`ChangeRecord` represents."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class ChangeStatus(str, Enum):
    """The outcome of processing one pushed change."""

    APPLIED = "applied"
    CONFLICT = "conflict"
    REJECTED = "rejected"


class SyncSequence(Base):
    """A single lock row, existing only to make :class:`ChangeRecord` writes gap-free.

    The problem this solves: :meth:`~server.services.sync_service.SyncService.pull_changes`
    resumes from an autoincrement :class:`ChangeRecord` id ("give me
    everything after id N"). Under PostgreSQL, a row's id is assigned
    when it is *inserted*, but the row only becomes visible to other
    transactions when it *commits* — and those two things can happen
    out of order for two concurrent transactions (a transaction that
    grabbed a lower id can commit *after* one that grabbed a higher
    id). A client that pulls at exactly the wrong moment would see the
    higher id, advance its cursor past it, and then permanently miss
    the lower-id row once it finally commits — silent, unrecoverable
    data loss, and the kind of bug that will never show up against
    SQLite (see :mod:`tests.test_server_phase7_1_hardening`'s
    docstring for why).

    The fix does not touch the cursor itself — ``id`` stays a plain
    autoincrement column, and pulling is still "everything after N."
    Instead, every code path that inserts an :attr:`~ChangeStatus.APPLIED`
    :class:`ChangeRecord` (:meth:`~server.services.sync_service.SyncService.push_changes`,
    :meth:`~server.services.sync_service.SyncService.resolve_conflict`)
    first takes an exclusive row lock on this table's single row via
    ``SELECT ... FOR UPDATE`` before inserting anything. Because only
    one transaction can hold that lock at a time, and every such
    transaction holds it until it commits or rolls back, applied
    inserts across the *entire server* are forced into the same order
    their ids are assigned in — a transaction cannot begin inserting
    until every earlier one has already committed (or rolled back).
    Id order and commit order become identical by construction, so
    "everything with id greater than N" can never skip a row: nothing
    with a lower id can still be uncommitted once a higher id has been
    assigned. See ``server/services/sync_service.py``'s module
    docstring for the full read/write protocol this fits into.

    Deliberately not a :class:`~server.database.base.ServerBaseModel`:
    this row is a locking primitive, not a business entity, so it gets
    none of ``public_id``/timestamps/soft-delete — those would only
    invite someone to query or "soft-delete" it, which makes no sense
    for something that must always have exactly one permanent row.
    """

    __tablename__ = "sync_sequence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)


class EntityVersion(ServerBaseModel):
    """The current known version of one synced entity.

    Attributes:
        entity_type: A stable string identifying what kind of entity
            this is (e.g. ``"customer"``) — a plain string rather than
            an enum, since new entity types arrive in later phases
            without needing a schema migration here.
        entity_id: The entity's public UUID, as a string.
        current_version: This entity's current version number, bumped
            by exactly one on every successfully
            :attr:`~ChangeStatus.APPLIED` change.
    """

    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", name="uq_entity_versions_entity_type_entity_id"),
    )

    # No standalone index=True on either column: the UniqueConstraint
    # above already creates a composite (entity_type, entity_id) index,
    # which also serves an entity_type-only lookup as a leftmost
    # prefix — a separate single-column index on either would only add
    # write overhead for a lookup pattern that column never actually
    # serves alone (see Phase 7's architecture review).
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ChangeRecord(ServerBaseModel):
    """One durable log entry: a pushed change and how it was resolved.

    Attributes:
        device_id: Which registered :class:`~server.models.device.SyncDevice`
            pushed this change.
        device: The associated :class:`~server.models.device.SyncDevice`.
        entity_type: Matches :attr:`EntityVersion.entity_type`.
        entity_id: Matches :attr:`EntityVersion.entity_id`.
        operation: What kind of change this is (see :class:`SyncOperation`).
        payload: The change's JSON-safe data. Opaque to this table —
            interpreting it is entirely the concern of whichever future
            phase wires a specific entity type into this mechanism.
        checksum: SHA-256 hex digest of ``payload``'s canonical JSON
            encoding, supplied by the pushing device and re-verified by
            :meth:`~server.services.sync_service.SyncService.push_changes`
            before anything else — a mismatch means the payload was
            corrupted or tampered with in transit and is rejected
            immediately, before conflict detection even runs.
        base_version: The version the device believed this entity was
            at when it made this change; ``0`` for a first-ever
            :attr:`~SyncOperation.CREATE`. Compared against
            :attr:`EntityVersion.current_version` to detect a conflict.
        new_version: The version this change results in once applied;
            ``None`` if :attr:`status` is not :attr:`~ChangeStatus.APPLIED`.
        status: The outcome (see :class:`ChangeStatus`). Once a row is
            written as :attr:`~ChangeStatus.APPLIED` it is never
            changed again — a resolved conflict appends a *new*
            ``APPLIED`` row rather than flipping this one's status, so
            a row's position in the id sequence always matches when it
            actually became pull-visible (see :class:`SyncSequence`).
            A ``CONFLICT`` row may still transition to ``REJECTED``
            (discarded) or stay ``CONFLICT`` forever if never resolved
            — neither transition affects anything already pulled,
            since only ``APPLIED`` rows are ever returned by
            :meth:`~server.services.sync_service.SyncService.pull_changes`.
        conflict_reason: A human-readable explanation, set when
            :attr:`status` is :attr:`~ChangeStatus.CONFLICT` or
            :attr:`~ChangeStatus.REJECTED`, and on the row a resolved
            conflict superseded, recording which new row replaced it.
    """

    __table_args__ = (Index("ix_change_records_status_id", "status", "id"),)

    device_id: Mapped[int] = mapped_column(ForeignKey("sync_devices.id"), nullable=False, index=True)
    device: Mapped["SyncDevice"] = relationship("SyncDevice")

    entity_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    operation: Mapped[SyncOperation] = mapped_column(
        enum_column_type(SyncOperation), nullable=False
    )
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)

    base_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # No standalone index=True here: the composite ix_change_records_status_id
    # above (declared in __table_args__) already covers every current
    # query that filters by status, including status-only lookups
    # (status is its leftmost column).
    status: Mapped[ChangeStatus] = mapped_column(enum_column_type(ChangeStatus), nullable=False)
    conflict_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

"""The generic change-tracking ledger that powers synchronization.

Two tables, both entity-type-agnostic — neither knows anything about
customers, licenses, or configuration, the same way
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
  from. Never updated in place except by
  :meth:`~server.services.sync_service.SyncService.resolve_conflict`,
  which is the only sanctioned way a ``CONFLICT``/``REJECTED`` row
  changes status after the fact.

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

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import enum_column_type
from server.database.base import ServerBaseModel
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

    entity_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
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
            at when it made this change; ``None`` for a first-ever
            :attr:`~SyncOperation.CREATE`. Compared against
            :attr:`EntityVersion.current_version` to detect a conflict.
        new_version: The version this change results in once applied;
            ``None`` if :attr:`status` is not :attr:`~ChangeStatus.APPLIED`.
        status: The outcome (see :class:`ChangeStatus`).
        conflict_reason: A human-readable explanation, set only when
            :attr:`status` is :attr:`~ChangeStatus.CONFLICT` or
            :attr:`~ChangeStatus.REJECTED`.
    """

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

    status: Mapped[ChangeStatus] = mapped_column(
        enum_column_type(ChangeStatus), nullable=False, index=True
    )
    conflict_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

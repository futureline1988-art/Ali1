"""Local, generic synchronization bookkeeping for the Developer Suite.

Four tables, all entity-type-agnostic — none of them know anything
about customers, licenses, or configuration, mirroring
:mod:`server.models.sync`'s own "the transport layer knows nothing
about any business domain" discipline on this side of the wire too:

* :class:`SyncDeviceCredential` — a singleton row holding this
  installation's own identity on the Attendance Server (issued once at
  registration; see :mod:`developer_suite.sync.client`).
* :class:`SyncCursor` — one row per ``entity_type``, the last
  :class:`~server.models.sync.ChangeRecord` id this installation has
  pulled and applied for that type. Deliberately per-``entity_type``
  rather than one global cursor: :meth:`~server.services.sync_service.SyncService.pull_changes`
  filtered by ``entity_type`` returns ``next_cursor`` as the highest id
  *among the returned rows*, not the highest id examined — mixing that
  cursor back into an unfiltered or differently-filtered pull would
  silently skip other types' changes. As long as one cursor is always
  used with the same ``entity_type`` filter, pulling stays gap-free for
  exactly the reason :class:`~server.models.sync.SyncSequence` makes
  the underlying id sequence gap-free in the first place.
* :class:`SyncEntityVersion` — one row per synced entity, mirroring
  :class:`server.models.sync.EntityVersion` on this side: what version
  this installation last confirmed the server is at for that entity,
  used to compute the ``base_version`` of the next outgoing change.
* :class:`SyncOutboxEntry` — at most one *pending* row per entity,
  coalescing every local edit made before the next successful push into
  a single outgoing change (see
  :meth:`~developer_suite.repositories.sync_repository.SyncOutboxRepository.enqueue`)
  rather than queuing one row per local edit. This is what keeps a
  burst of local edits from ever producing two outbox rows for the same
  entity in one push batch with the same stale ``base_version`` — a
  batch-internal version race the append-only ledger design on the
  server side does not, by itself, protect a naive client against.

None of these four tables is specific to :class:`~developer_suite.models.customer.Customer`
— any future synced entity (employees, attendance, departments,
licenses, settings, ...) reuses all four exactly as they are, keyed by
its own ``entity_type`` string. See
:mod:`developer_suite.sync.coordinator` for what actually reads and
writes them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import JSON, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from developer_suite.database.base import Base
from developer_suite.models.encrypted_types import EncryptedString
from models.base import UTCDateTime, enum_column_type


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class SyncOperation(str, Enum):
    """What kind of local change a :class:`SyncOutboxEntry` represents.

    Values match :class:`~server.models.sync.SyncOperation` exactly —
    a protocol contract this application replicates rather than
    imports (see :mod:`developer_suite.sync.protocol`'s docstring for
    why a direct Python import from ``server`` would be the wrong kind
    of reuse for a boundary meant to be crossed over HTTP).
    """

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class OutboxStatus(str, Enum):
    """The lifecycle of one queued, not-yet-confirmed local change."""

    PENDING = "pending"
    PUSHED = "pushed"
    CONFLICT = "conflict"
    REJECTED = "rejected"


class SyncDeviceCredential(Base):
    """This installation's own identity and credential on the Attendance Server.

    Exactly one row ever exists — the same "singleton lock/identity
    row" shape :class:`~server.models.sync.SyncSequence` uses on the
    server, for the same reason: this is an infrastructure primitive,
    not a business entity, so it gets none of
    :class:`~developer_suite.database.base.DeveloperSuiteBaseModel`'s
    ``public_id``/timestamps/soft-delete.

    Attributes:
        device_public_id: This installation's device UUID, as issued
            by ``POST /api/v1/devices/register`` (see
            :func:`~developer_suite.sync.client.register_device`) —
            sent back on every push/pull as the ``X-Device-Id`` header.
        api_key: This installation's plaintext sync credential,
            encrypted at rest (see
            :mod:`developer_suite.models.encrypted_types`). Unlike the
            server's own :attr:`~server.models.device.SyncDevice.api_key_hash`,
            this cannot be a one-way hash: the credential must be
            recoverable in plaintext to populate the ``X-Device-Api-Key``
            header on every future call.
        server_url: The Attendance Server base URL this credential was
            issued by, recorded so a misconfigured
            :attr:`~developer_suite.config.DeveloperSuiteConfig.attendance_server_url`
            pointing at a *different* server is at least detectable
            rather than silently sending a credential to the wrong
            place.
        registered_at: When this installation enrolled.
    """

    __tablename__ = "sync_device_credential"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_public_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    api_key: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    server_url: Mapped[str] = mapped_column(String(500), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utc_now)


class SyncCursor(Base):
    """The last pulled-and-applied change id for one ``entity_type``.

    Attributes:
        entity_type: The synced entity type this cursor tracks.
        last_change_id: The highest :class:`~server.models.sync.ChangeRecord`
            id this installation has successfully applied for
            ``entity_type``; ``0`` means "never pulled."
    """

    __tablename__ = "sync_cursors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    last_change_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SyncEntityVersion(Base):
    """This installation's last confirmed server-side version of one entity.

    Attributes:
        entity_type: A stable string identifying what kind of entity
            this is (e.g. ``"customer"``).
        entity_id: The entity's public UUID, as a string — the same id
            space :class:`~server.models.sync.EntityVersion.entity_id`
            uses.
        known_version: The version this installation last confirmed
            the server is at for this entity; the ``base_version`` of
            the next outgoing change for it.
    """

    __tablename__ = "sync_entity_versions"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", name="uq_sync_entity_versions_entity_type_entity_id"),
    )

    # No standalone index=True on either column: the UniqueConstraint
    # above already creates a composite index that also serves an
    # entity_type-only lookup as a leftmost prefix (same reasoning as
    # server.models.sync.EntityVersion).
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    known_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SyncOutboxEntry(Base):
    """At most one queued, not-yet-confirmed local change per entity.

    Attributes:
        entity_type: A stable string identifying what kind of entity
            this is.
        entity_id: The entity's public UUID, as a string.
        operation: What kind of change this represents right now — may
            be escalated in place as further local edits coalesce into
            this same row (see
            :meth:`~developer_suite.repositories.sync_repository.SyncOutboxRepository.enqueue`).
        payload: The change's JSON-safe data, in the same shape
            :meth:`~developer_suite.models.customer.Customer.to_dict`
            (or any future synced model's) produces.
        base_version: The version this installation believed the
            entity was at when this row was first created — frozen at
            that moment even as later local edits coalesce into the
            same row, since it must describe the state *before* any of
            the edits this row now represents.
        checksum: SHA-256 hex digest of ``payload``, computed the same
            way :meth:`~server.services.sync_service.SyncService.compute_checksum`
            will re-verify it.
        status: This row's lifecycle (see :class:`OutboxStatus`).
        conflict_reason: Set when :attr:`status` is
            :attr:`~OutboxStatus.CONFLICT` or
            :attr:`~OutboxStatus.REJECTED`, echoing the server's own
            explanation.
        created_at: When this row was first created (i.e. when the
            *first* coalesced local edit happened).
        updated_at: When this row was last modified (a later coalesced
            edit, or a push attempt's outcome).
    """

    __tablename__ = "sync_outbox_entries"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", name="uq_sync_outbox_entries_entity_type_entity_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[SyncOperation] = mapped_column(enum_column_type(SyncOperation), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    base_version: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[OutboxStatus] = mapped_column(
        enum_column_type(OutboxStatus), nullable=False, default=OutboxStatus.PENDING, index=True
    )
    conflict_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

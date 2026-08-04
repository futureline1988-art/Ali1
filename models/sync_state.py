"""Local synchronization bookkeeping for the Attendance Client's own installation.

Phase 13 gives the Attendance Client itself, for the first time, code
that talks to the Attendance Server (see ``sync/protocol.py``'s module
docstring for why this package replicates rather than imports
:mod:`developer_suite.sync`'s equivalent modules). This installation
only ever *pulls* configuration published by the Developer Suite — it
never pushes anything back — so only the two tables a pull-only client
needs exist here, the same infrastructure-primitive shape
:class:`~developer_suite.models.sync_state.SyncDeviceCredential` and
:class:`~developer_suite.models.sync_state.SyncCursor` use on the
Developer Suite side:

* :class:`ClientSyncCredential` — a singleton row holding this
  installation's own identity on the Attendance Server, issued once at
  enrollment (see ``sync/coordinator.py``).
* :class:`ClientSyncCursor` — one row per ``entity_type``, the last
  change id this installation has pulled and applied for that type.
  Only ``"company_configuration"`` is used today, but the table stays
  entity-type-agnostic like its Developer Suite counterpart.

Both extend :class:`~models.base.Base` directly rather than
:class:`~models.base.BaseModel`: they are infrastructure bookkeeping,
not company-scoped business data, so they get none of
``BaseModel``'s ``public_id``/timestamps/soft-delete/audit columns —
exactly the same reasoning
:class:`~developer_suite.models.sync_state.SyncDeviceCredential`'s own
docstring gives.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, UTCDateTime
from models.encrypted_types import EncryptedString


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class ClientSyncCredential(Base):
    """This installation's own identity and credential on the Attendance Server.

    Exactly one row ever exists.

    Attributes:
        device_public_id: This installation's device UUID, as issued
            by ``POST /api/v1/devices/register`` at enrollment.
        api_key: This installation's plaintext sync credential,
            encrypted at rest (see :mod:`models.encrypted_types`) —
            must be recoverable in plaintext to populate the
            ``X-Device-Api-Key`` header on every pull.
        server_url: The Attendance Server base URL this credential was
            issued by.
        registered_at: When this installation enrolled.
    """

    __tablename__ = "client_sync_credential"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_public_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    api_key: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    server_url: Mapped[str] = mapped_column(String(500), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utc_now)


class ClientSyncCursor(Base):
    """The last pulled-and-applied change id for one ``entity_type``.

    Attributes:
        entity_type: The synced entity type this cursor tracks (e.g.
            ``"company_configuration"``).
        last_change_id: The highest change id this installation has
            successfully applied for ``entity_type``; ``0`` means
            "never pulled."
    """

    __tablename__ = "client_sync_cursors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    last_change_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

"""The Phase 10 administrative-token bootstrap record.

**Temporary**, by explicit design: until a real authentication/login
flow exists for the Developer Suite (see
``server/api/routers/devices.py``'s docstring — no such flow exists
anywhere in the platform yet), the read-only administration endpoints
Phase 10 adds (``GET /api/v1/devices``, ``GET /api/v1/sync/activity``,
``GET /api/v1/status``) still require a ``sync:admin``-scoped bearer
token like every other administrative endpoint (Phase 10 explicitly
keeps them protected, reusing the existing token infrastructure rather
than loosening it). :class:`AdminBootstrapToken` is where that token
is kept: read once from the ``DEV_SUITE_SYNC_ADMIN_TOKEN`` environment
variable and persisted here, encrypted at rest, so it does not need to
be re-supplied via the environment on every run (see
:mod:`developer_suite.admin.token_provider` for the read/persist
logic).

This table — and everything in :mod:`developer_suite.admin` that reads
it — exists to be deleted outright once a real login flow replaces it;
see that package's docstring for the abstraction boundary
(:class:`~developer_suite.admin.token_provider.AdminTokenProvider`)
that makes the replacement a one-class swap.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from developer_suite.database.base import Base
from developer_suite.models.encrypted_types import EncryptedString
from models.base import UTCDateTime


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AdminBootstrapToken(Base):
    """A singleton row holding this installation's bootstrap admin token.

    Exactly one row ever exists — the same "singleton identity row"
    shape :class:`~developer_suite.models.sync_state.SyncDeviceCredential`
    already uses for this installation's own sync device credential,
    for the same reason: this is a local bootstrap secret, not a
    business entity.

    Attributes:
        token: The plaintext ``sync:admin``-scoped bearer token,
            encrypted at rest (see
            :mod:`developer_suite.models.encrypted_types`) — must be
            recoverable in plaintext to populate the ``Authorization``
            header on every admin API call, so (like
            :attr:`~developer_suite.models.sync_state.SyncDeviceCredential.api_key`)
            it cannot be a one-way hash.
        saved_at: When this token was last (re)written.
    """

    __tablename__ = "admin_bootstrap_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    saved_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utc_now)

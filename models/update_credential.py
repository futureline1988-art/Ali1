"""Optional, locally-configured credential for the Attendance Client's software-update checks.

Unlike the retired multi-tenant Company-Code enrollment this project
used to require for *every* feature (subscription checks, remote
configuration, and update checks all shared one device credential),
software-update checking is now entirely separate and entirely
optional: a row here only ever exists if an administrator manually
configures a vendor update-server connection. No UI writes this table
yet — until one does, :class:`~repositories.update_credential_repository.UpdateCredentialRepository.get`
returns ``None`` and :class:`~updates.checker.UpdateCheckService` reports
"not configured" rather than fabricating a connection that does not
exist (see :mod:`updates`'s own docstring).

Extends :class:`~models.base.Base` directly rather than
:class:`~models.base.BaseModel`: infrastructure bookkeeping, not
company-scoped business data, exactly like the retired
``models.sync_state.ClientSyncCredential`` this replaces.
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


class UpdateServerCredential(Base):
    """This installation's optional identity/credential for update checks only.

    Exactly one row ever exists, if any.

    Attributes:
        device_public_id: This installation's identifier on the
            configured update server.
        api_key: This installation's plaintext update-check credential,
            encrypted at rest.
        server_url: The update server's base URL.
        registered_at: When this credential was configured.
    """

    __tablename__ = "update_server_credential"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_public_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    api_key: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    server_url: Mapped[str] = mapped_column(String(500), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utc_now)

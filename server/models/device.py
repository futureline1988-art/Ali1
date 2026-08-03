"""Registered device (Attendance Client or Developer Suite installation).

A "device" is one installation of either application that the
Attendance Server has issued sync credentials to — the unit
:mod:`server.services.sync_service` authenticates push/pull calls
against. Registering a device is an administrative action (see
:mod:`server.services.device_service`); nothing here decides *what*
data a device is allowed to sync — that is entirely the concern of
whichever future phase wires a specific business domain into the
generic change-tracking mechanism in :mod:`server.models.sync`.

Named :class:`SyncDevice` rather than plainly ``Device`` deliberately:
this schema is fully independent of the Attendance Client's own
``models.device.Device`` (a *biometric fingerprint device* registry, a
completely unrelated concept), so a bare ``Device`` here — though
technically harmless, since the two live in separate schemas/databases
— would read as confusingly overloaded to anyone working across both
codebases.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import UTCDateTime, enum_column_type
from server.database.base import ServerBaseModel


class DeviceType(str, Enum):
    """Which application a registered device is an installation of."""

    DEVELOPER_SUITE = "developer_suite"
    ATTENDANCE_CLIENT = "attendance_client"


class SyncDevice(ServerBaseModel):
    """One registered Attendance Client or Developer Suite installation.

    Attributes:
        name: A human-readable label (e.g. a company name for an
            Attendance Client installation, or a machine name for a
            Developer Suite installation).
        device_type: Which application this device is (see
            :class:`DeviceType`).
        api_key_hash: A bcrypt hash of this device's sync credential
            (see :meth:`~server.services.device_service.DeviceService.register_device`)
            — the plaintext key is generated once at registration,
            returned to the caller, and never stored or recoverable
            again, the same "generate once, never retrievable" pattern
            :mod:`licensing.crypto.signing` already established for
            the vendor's signing key.
        is_active: Whether this device's credential is still accepted;
            set to ``False`` instead of deleting the row so its sync
            history remains attributable.
        last_seen_at: Timestamp of the most recent successful push or
            pull from this device.
    """

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    device_type: Mapped[DeviceType] = mapped_column(
        enum_column_type(DeviceType), nullable=False, index=True
    )
    api_key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

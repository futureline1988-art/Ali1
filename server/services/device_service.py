"""Device registration and authentication.

Reuses :func:`utils.security.hash_password`/:func:`utils.security.verify_password`
(bcrypt) and :func:`utils.security.generate_session_token` (a
cryptographically secure random string) directly rather than
reimplementing credential hashing or random generation — the same
primitives the Attendance Client already uses for user passwords and
session tokens, applied here to a device's sync credential instead.
Every call to :func:`~utils.security.hash_password` passes this
server's own :attr:`~server.config.ServerConfig.security` settings
explicitly, never falling back to :func:`config.get_config`'s bcrypt
cost factor.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from utils.security import generate_session_token, hash_password, verify_password

from database.database import Database
from server.config import ServerConfig
from server.models.device import SyncDevice, DeviceType
from server.repositories.device_repository import DeviceRepository
from server.services.base_service import BaseService


class DeviceServiceError(Exception):
    """Base class for device operation failures the API layer should translate."""


class DeviceNotFoundError(DeviceServiceError):
    """No device exists with the given id."""


class DeviceService(BaseService):
    """Register, authenticate, and manage devices."""

    def __init__(self, database: Database, *, config: ServerConfig) -> None:
        """Create a device service bound to ``database`` and this server's configuration.

        Args:
            database: This server's own database.
            config: This server's configuration; supplies the bcrypt
                cost factor for hashing device credentials.
        """
        super().__init__(database)
        self._config = config

    def register_device(self, *, name: str, device_type: DeviceType) -> tuple[SyncDevice, str]:
        """Register a new device and issue its one-time sync credential.

        Args:
            name: A human-readable label for this device.
            device_type: Which application this device is an
                installation of.

        Returns:
            A ``(device, api_key)`` pair. ``api_key`` is the plaintext
            credential — returned here and only here; only its bcrypt
            hash is ever stored, so a caller that loses it must
            register a new device rather than recover the old key.
        """
        api_key = generate_session_token()
        with self._session_scope() as session:
            device = SyncDevice(
                name=name,
                device_type=device_type,
                api_key_hash=hash_password(api_key, rounds=self._config.security.bcrypt_rounds),
                is_active=True,
            )
            DeviceRepository(session).add(device)
            return device, api_key

    def authenticate_device(self, public_id: uuid.UUID, api_key: str) -> SyncDevice | None:
        """Verify a device's credential and, on success, update its last-seen timestamp.

        Args:
            public_id: The device's public UUID (from the
                ``X-Device-Id`` header).
            api_key: The plaintext credential to verify (from the
                ``X-Device-Api-Key`` header).

        Returns:
            The authenticated device, or ``None`` if ``public_id`` is
            unknown, the device is inactive/deleted, or ``api_key``
            does not match.
        """
        with self._session_scope() as session:
            repo = DeviceRepository(session)
            device = repo.get_active_by_public_id(public_id)
            if device is None:
                return None
            if not verify_password(api_key, device.api_key_hash):
                return None
            device.last_seen_at = datetime.now(timezone.utc)
            session.flush()
            return device

    def deactivate_device(self, device_id: int) -> SyncDevice:
        """Revoke a device's credential without deleting its sync history.

        Args:
            device_id: The device to deactivate.

        Returns:
            The updated device.

        Raises:
            DeviceNotFoundError: No device exists with that id.
        """
        with self._session_scope() as session:
            device = DeviceRepository(session).get_by_id(device_id)
            if device is None:
                raise DeviceNotFoundError(f"No device with id={device_id!r}.")
            device.is_active = False
            session.flush()
            return device

    def list_devices(self) -> list[SyncDevice]:
        """List every registered, non-deleted device."""
        with self._session_scope() as session:
            return DeviceRepository(session).list_all()

    def get_device(self, device_id: int) -> SyncDevice | None:
        """Fetch a single device by id, or ``None`` if not found."""
        with self._session_scope() as session:
            return DeviceRepository(session).get_by_id(device_id)

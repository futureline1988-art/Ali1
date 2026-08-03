"""Repository for :class:`~server.models.device.SyncDevice`."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.models.device import SyncDevice
from server.repositories.base_repository import BaseRepository


class DeviceRepository(BaseRepository[SyncDevice]):
    """Data access for :class:`~server.models.device.SyncDevice`."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        super().__init__(session, model=SyncDevice)

    def get_active_by_public_id(self, public_id: uuid.UUID) -> SyncDevice | None:
        """Fetch a single active, non-deleted device by its public UUID.

        Args:
            public_id: The device's ``public_id``.

        Returns:
            The matching device, or ``None`` if not found, soft-deleted,
            or deactivated (see
            :meth:`~server.services.device_service.DeviceService.deactivate_device`).
        """
        statement = select(SyncDevice).where(
            SyncDevice.public_id == public_id,
            SyncDevice.is_deleted.is_(False),
            SyncDevice.is_active.is_(True),
        )
        return self.session.execute(statement).scalar_one_or_none()

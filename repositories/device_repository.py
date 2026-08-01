"""Repository for :class:`~models.device.Device`."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.device import Device
from repositories.base_repository import CompanyScopedRepository


class DeviceRepository(CompanyScopedRepository[Device]):
    """Data access for :class:`~models.device.Device`, scoped to one company."""

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create a repository bound to ``session`` and ``company_id``."""
        super().__init__(session, model=Device, company_id=company_id)

    def get_by_name(self, name: str) -> Device | None:
        """Fetch a device by its unique-per-company name.

        Args:
            name: The device's :attr:`~models.device.Device.name`.

        Returns:
            The matching device, or ``None``.
        """
        statement = select(Device).where(
            Device.company_id == self.company_id,
            Device.name == name,
            Device.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

    def list_active(self) -> list[Device]:
        """List every device eligible for scheduled sync jobs.

        Returns:
            Devices with :attr:`~models.device.Device.is_active` set,
            ordered by name.
        """
        statement = (
            select(Device)
            .where(
                Device.company_id == self.company_id,
                Device.is_active.is_(True),
                Device.is_deleted.is_(False),
            )
            .order_by(Device.name)
        )
        return list(self.session.execute(statement).scalars().all())

    def list_by_branch(self, branch_id: int) -> list[Device]:
        """List every device installed at one branch.

        Args:
            branch_id: The branch's id.

        Returns:
            Matching devices, ordered by name.
        """
        statement = (
            select(Device)
            .where(
                Device.company_id == self.company_id,
                Device.branch_id == branch_id,
                Device.is_deleted.is_(False),
            )
            .order_by(Device.name)
        )
        return list(self.session.execute(statement).scalars().all())

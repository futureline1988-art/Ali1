"""Repository for :class:`~server.models.subscription.Subscription`."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.models.device import DeviceType, SyncDevice
from server.models.subscription import Subscription
from server.repositories.base_repository import BaseRepository


class SubscriptionRepository(BaseRepository[Subscription]):
    """Data access for :class:`~server.models.subscription.Subscription`."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        super().__init__(session, model=Subscription)

    def get_by_company_name(self, company_name: str) -> Subscription | None:
        """Fetch a single, non-deleted subscription by its exact company name.

        Args:
            company_name: The subscription's :attr:`~server.models.subscription.Subscription.company_name`.

        Returns:
            The matching subscription, or ``None`` if not found.
        """
        statement = select(Subscription).where(
            Subscription.company_name == company_name, Subscription.is_deleted.is_(False)
        )
        return self.session.execute(statement).scalar_one_or_none()

    def get_by_company_code(self, company_code: str) -> Subscription | None:
        """Fetch a single, non-deleted subscription by its exact company code.

        The lookup an Attendance Client's self-registration uses (see
        :meth:`~server.services.device_service.DeviceService.self_register_device`)
        — unlike :meth:`get_by_company_name`, this is reachable from an
        unauthenticated request, so callers must not let a caller
        distinguish "no such code" from "code exists but inactive"
        (see that method's own docstring).

        Args:
            company_code: The subscription's :attr:`~server.models.subscription.Subscription.company_code`.

        Returns:
            The matching subscription, or ``None`` if not found.
        """
        statement = select(Subscription).where(
            Subscription.company_code == company_code, Subscription.is_deleted.is_(False)
        )
        return self.session.execute(statement).scalar_one_or_none()

    def count_active_devices(self, subscription_id: int) -> int:
        """Count active, non-deleted Attendance Client devices linked to a subscription.

        Args:
            subscription_id: The subscription's id.

        Returns:
            How many devices currently count against
            :attr:`~server.models.subscription.Subscription.max_devices`.
        """
        statement = (
            select(func.count())
            .select_from(SyncDevice)
            .where(
                SyncDevice.subscription_id == subscription_id,
                SyncDevice.device_type == DeviceType.ATTENDANCE_CLIENT,
                SyncDevice.is_active.is_(True),
                SyncDevice.is_deleted.is_(False),
            )
        )
        return self.session.execute(statement).scalar_one()

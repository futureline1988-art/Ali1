"""Create, renew, suspend, reactivate, and list company subscriptions.

Every method here is a thin, error-translating wrapper over
:class:`~developer_suite.admin.client.AdminApiClient`'s subscription
methods — mirrors :class:`~developer_suite.services.update_manager_service.UpdateManagerService`'s
``_call()`` pattern exactly, so the UI only ever needs to catch
:class:`SubscriptionServiceError`. This is the server-managed
replacement for the retired file-based licensing system: nothing here
signs or encodes anything — a subscription's validity is a plain
database row the Attendance Server itself evaluates.
"""

from __future__ import annotations

from datetime import date
from typing import Callable, TypeVar

from developer_suite.admin.client import AdminApiClient, AdminApiError, SubscriptionInfo

_T = TypeVar("_T")


class SubscriptionServiceError(Exception):
    """Base class for subscription operation failures the UI should display."""


class SubscriptionService:
    """Create, renew, suspend, reactivate, and list subscriptions."""

    def __init__(self, admin_client: AdminApiClient) -> None:
        """Create a subscription service bound to an admin client.

        Args:
            admin_client: Performs every actual HTTP call against the
                Attendance Server's subscription-management endpoints.
        """
        self._admin_client = admin_client

    def create_subscription(
        self,
        *,
        company_name: str,
        subscription_start_date: date,
        subscription_end_date: date,
        max_devices: int,
        max_users: int | None = None,
    ) -> SubscriptionInfo:
        """Create a new subscription for a company.

        Raises:
            SubscriptionServiceError: The server rejected the request
                (e.g. a subscription already exists for that company
                name) or could not be reached.
        """
        return self._call(
            lambda: self._admin_client.create_subscription(
                company_name=company_name,
                subscription_start_date=subscription_start_date,
                subscription_end_date=subscription_end_date,
                max_devices=max_devices,
                max_users=max_users,
            )
        )

    def list_subscriptions(self) -> list[SubscriptionInfo]:
        """Fetch every subscription, each with its current device count."""
        return self._call(self._admin_client.list_subscriptions)

    def get_subscription(self, subscription_id: int) -> SubscriptionInfo:
        """Fetch a single subscription by id."""
        return self._call(lambda: self._admin_client.get_subscription(subscription_id))

    def renew_subscription(self, subscription_id: int, *, new_end_date: date) -> SubscriptionInfo:
        """Extend a subscription's end date, without changing its suspend/active status."""
        return self._call(
            lambda: self._admin_client.renew_subscription(subscription_id, new_end_date=new_end_date)
        )

    def suspend_subscription(self, subscription_id: int) -> SubscriptionInfo:
        """Suspend a subscription immediately."""
        return self._call(lambda: self._admin_client.suspend_subscription(subscription_id))

    def reactivate_subscription(self, subscription_id: int) -> SubscriptionInfo:
        """Reactivate a suspended subscription."""
        return self._call(lambda: self._admin_client.reactivate_subscription(subscription_id))

    def update_limits(
        self, subscription_id: int, *, max_devices: int | None = None, max_users: int | None = None
    ) -> SubscriptionInfo:
        """Change a subscription's device/user caps."""
        return self._call(
            lambda: self._admin_client.update_subscription_limits(
                subscription_id, max_devices=max_devices, max_users=max_users
            )
        )

    def clear_max_users(self, subscription_id: int) -> SubscriptionInfo:
        """Explicitly set a subscription's user cap back to unlimited."""
        return self._call(lambda: self._admin_client.clear_subscription_max_users(subscription_id))

    @staticmethod
    def _call(operation: Callable[[], _T]) -> _T:
        """Invoke one :class:`~developer_suite.admin.client.AdminApiClient` call, translating its errors."""
        try:
            return operation()
        except AdminApiError as exc:
            raise SubscriptionServiceError(str(exc)) from exc

"""Subscription Manager module.

The server-managed replacement for the retired ``LicenseManagerModule``:
create/renew/suspend/reactivate company subscriptions via
:mod:`developer_suite.services.subscription_service`, which itself
talks to the Attendance Server's ``/api/v1/subscriptions`` endpoints
(:mod:`server.api.routers.subscriptions`) rather than signing anything
locally — see :mod:`server.models.subscription`'s own docstring for
why this system replaced the old Ed25519-signed license-key format.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from developer_suite.modules.base import PlatformModule
from developer_suite.services.customer_service import CustomerService
from developer_suite.services.subscription_service import SubscriptionService
from developer_suite.ui.subscription_management_page import SubscriptionManagementPage


class SubscriptionManagerModule(PlatformModule):
    """Subscription creation, renewal, suspension, and reactivation."""

    def __init__(self, subscription_service: SubscriptionService, customer_service: CustomerService) -> None:
        """Create the module bound to a subscription service and a customer service.

        Args:
            subscription_service: Performs every subscription operation
                this module's page needs.
            customer_service: Used only to populate the "create
                subscription" company-name picker.
        """
        self._subscription_service = subscription_service
        self._customer_service = customer_service

    @property
    def module_id(self) -> str:
        return "subscription_manager"

    @property
    def display_name_ar(self) -> str:
        return "إدارة الاشتراكات"

    @property
    def display_name_en(self) -> str:
        return "Subscription Manager"

    def build_page(self) -> QWidget:
        return SubscriptionManagementPage(self._subscription_service, self._customer_service)

"""Customer Management module.

Phase 3: create/edit/delete (soft) customer, search, company status
(active/suspended), contact information, notes — all stored in the
Developer Suite's own database (see this package's parent
``__init__.py`` for the ownership boundary: customer *accounts*, not
customer *operational* data). Phase 10 adds a read-focused customer
details view (subscription status, synchronization status) on top of
this same page — see :mod:`developer_suite.ui.customer_details_dialog`.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from developer_suite.modules.base import PlatformModule
from developer_suite.services.customer_service import CustomerService
from developer_suite.services.subscription_service import SubscriptionService
from developer_suite.sync.coordinator import SyncCoordinator
from developer_suite.ui.customer_management_page import CustomerManagementPage


class CustomerManagementModule(PlatformModule):
    """Customer registry: CRUD, search, active/suspended status, and details view."""

    def __init__(
        self,
        customer_service: CustomerService,
        subscription_service: SubscriptionService,
        sync_coordinator: SyncCoordinator,
    ) -> None:
        """Create the module bound to its dependencies.

        Args:
            customer_service: Performs every customer operation this
                module's page needs.
            subscription_service: Passed through to the customer
                details dialog for its subscription tab.
            sync_coordinator: Passed through to the customer details
                dialog for its synchronization-status field.
        """
        self._customer_service = customer_service
        self._subscription_service = subscription_service
        self._sync_coordinator = sync_coordinator

    @property
    def module_id(self) -> str:
        return "customer_management"

    @property
    def display_name_ar(self) -> str:
        return "إدارة العملاء"

    @property
    def display_name_en(self) -> str:
        return "Customer Management"

    def build_page(self) -> QWidget:
        return CustomerManagementPage(self._customer_service, self._subscription_service, self._sync_coordinator)

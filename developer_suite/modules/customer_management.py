"""Customer Management module.

Phase 3: create/edit/delete (soft) customer, search, company status
(active/suspended), contact information, notes — all stored in the
Developer Suite's own database (see this package's parent
``__init__.py`` for the ownership boundary: customer *accounts*, not
customer *operational* data). No remote synchronization, licensing UI,
update management, or monitoring — those remain out of scope until
their own approved phases.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from developer_suite.modules.base import PlatformModule
from developer_suite.services.customer_service import CustomerService
from developer_suite.ui.customer_management_page import CustomerManagementPage


class CustomerManagementModule(PlatformModule):
    """Customer registry: CRUD, search, and active/suspended status."""

    def __init__(self, customer_service: CustomerService) -> None:
        """Create the module bound to a customer service.

        Args:
            customer_service: Performs every customer operation this
                module's page needs.
        """
        self._customer_service = customer_service

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
        return CustomerManagementPage(self._customer_service)

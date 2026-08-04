"""Update Manager module.

Phase 14: real software-update distribution — create versions, sign
and upload setup/portable packages, target them at all customers,
specific customers, or a customer group, schedule/publish/disable, and
roll back — see :mod:`developer_suite.ui.update_manager_page`.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from developer_suite.admin.client import AdminApiClient
from developer_suite.modules.base import PlatformModule
from developer_suite.services.customer_group_service import CustomerGroupService
from developer_suite.services.customer_service import CustomerService
from developer_suite.services.update_manager_service import UpdateManagerService
from developer_suite.ui.update_manager_page import UpdateManagerPage


class UpdateManagerModule(PlatformModule):
    """Create, sign/upload, target, publish, schedule, disable, and roll back software updates."""

    def __init__(
        self,
        update_manager_service: UpdateManagerService,
        customer_service: CustomerService,
        customer_group_service: CustomerGroupService,
        admin_client: AdminApiClient,
    ) -> None:
        """Create the module bound to its dependencies.

        Args:
            update_manager_service: Performs every update-management
                operation this module's page needs.
            customer_service: Populates the "specific customers"
                target picker.
            customer_group_service: Populates the "customer group"
                target picker.
            admin_client: Populates the device-targeting list.
        """
        self._update_manager_service = update_manager_service
        self._customer_service = customer_service
        self._customer_group_service = customer_group_service
        self._admin_client = admin_client

    @property
    def module_id(self) -> str:
        return "update_manager"

    @property
    def display_name_ar(self) -> str:
        return "إدارة التحديثات"

    @property
    def display_name_en(self) -> str:
        return "Update Manager"

    def build_page(self) -> QWidget:
        return UpdateManagerPage(
            self._update_manager_service,
            self._customer_service,
            self._customer_group_service,
            self._admin_client,
        )

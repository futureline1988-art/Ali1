"""Developer Dashboard module: the main landing page (Phase 10; expanded Phase 12)."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from developer_suite.modules.base import PlatformModule
from developer_suite.services.customer_service import CustomerService
from developer_suite.services.dashboard_refresh_service import DashboardRefreshService
from developer_suite.services.license_service import LicenseService
from developer_suite.ui.dashboard_page import DashboardPage


class DashboardModule(PlatformModule):
    """The main dashboard — platform-wide counts, activity, charts, and quick actions."""

    def __init__(
        self,
        refresh_service: DashboardRefreshService,
        customer_service: CustomerService,
        license_service: LicenseService,
    ) -> None:
        """Create the module bound to its dependencies.

        Args:
            refresh_service: Supplies every displayed snapshot,
                computed off the UI thread.
            customer_service: Backs the quick actions panel's
                customer-related actions.
            license_service: Backs the quick actions panel's
                license-related actions.
        """
        self._refresh_service = refresh_service
        self._customer_service = customer_service
        self._license_service = license_service

    @property
    def module_id(self) -> str:
        return "dashboard"

    @property
    def display_name_ar(self) -> str:
        return "لوحة التحكم"

    @property
    def display_name_en(self) -> str:
        return "Dashboard"

    def build_page(self) -> QWidget:
        return DashboardPage(self._refresh_service, self._customer_service, self._license_service)

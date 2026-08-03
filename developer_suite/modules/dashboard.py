"""Developer Dashboard module: the main landing page (Phase 10)."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from developer_suite.modules.base import PlatformModule
from developer_suite.services.dashboard_service import DashboardService
from developer_suite.ui.dashboard_page import DashboardPage


class DashboardModule(PlatformModule):
    """The main dashboard — platform-wide counts and status at a glance."""

    def __init__(self, dashboard_service: DashboardService) -> None:
        """Create the module bound to its service.

        Args:
            dashboard_service: The service every displayed number comes from.
        """
        self._dashboard_service = dashboard_service

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
        return DashboardPage(self._dashboard_service)

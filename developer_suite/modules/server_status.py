"""Server Status module: a read-only view of the Attendance Server's own health (Phase 10)."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from developer_suite.admin.client import AdminApiClient
from developer_suite.config import DeveloperSuiteConfig
from developer_suite.modules.base import PlatformModule
from developer_suite.ui.server_status_page import ServerStatusPage


class ServerStatusModule(PlatformModule):
    """Read-only Attendance Server health: version, database, uptime, connected installations."""

    def __init__(self, admin_client: AdminApiClient, config: DeveloperSuiteConfig) -> None:
        """Create the module bound to its dependencies.

        Args:
            admin_client: The read-only client the page queries.
            config: Supplies this installation's own version.
        """
        self._admin_client = admin_client
        self._config = config

    @property
    def module_id(self) -> str:
        return "server_status"

    @property
    def display_name_ar(self) -> str:
        return "حالة الخادم"

    @property
    def display_name_en(self) -> str:
        return "Server Status"

    def build_page(self) -> QWidget:
        return ServerStatusPage(self._admin_client, self._config)

"""Monitoring module.

Phase 10 gives this module its real page: read-only visibility over
registered devices' online/offline state, recent registrations, and
recent synchronization activity/failures — see
:mod:`developer_suite.ui.monitoring_page`. No remote actions of any
kind; every control reloads a read.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from developer_suite.admin.client import AdminApiClient
from developer_suite.modules.base import PlatformModule
from developer_suite.ui.monitoring_page import MonitoringPage


class MonitoringModule(PlatformModule):
    """Read-only monitoring: device connectivity and recent synchronization activity."""

    def __init__(self, admin_client: AdminApiClient) -> None:
        """Create the module bound to its dependency.

        Args:
            admin_client: The read-only client the page queries.
        """
        self._admin_client = admin_client

    @property
    def module_id(self) -> str:
        return "monitoring"

    @property
    def display_name_ar(self) -> str:
        return "المراقبة"

    @property
    def display_name_en(self) -> str:
        return "Monitoring"

    def build_page(self) -> QWidget:
        return MonitoringPage(self._admin_client)

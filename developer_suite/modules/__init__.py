"""The platform modules, implementations of :class:`~developer_suite.modules.base.PlatformModule`.

Customer Management (Phase 3), License Manager (Phase 4), Monitoring
and Server Status (Phase 10) now have real business logic; Remote
Configuration and Update Manager remain "coming soon" placeholders
until their own approved phases. The Dashboard (Phase 10) is first in
:data:`ALL_MODULES`, making it the main window's default landing page.
"""

from __future__ import annotations

from developer_suite.modules.base import PlatformModule
from developer_suite.modules.customer_management import CustomerManagementModule
from developer_suite.modules.dashboard import DashboardModule
from developer_suite.modules.license_manager import LicenseManagerModule
from developer_suite.modules.monitoring import MonitoringModule
from developer_suite.modules.remote_configuration import RemoteConfigurationModule
from developer_suite.modules.server_status import ServerStatusModule
from developer_suite.modules.update_manager import UpdateManagerModule

ALL_MODULES: tuple[type[PlatformModule], ...] = (
    DashboardModule,
    CustomerManagementModule,
    LicenseManagerModule,
    RemoteConfigurationModule,
    MonitoringModule,
    ServerStatusModule,
    UpdateManagerModule,
)
"""Every platform module, in navigation display order."""

__all__ = [
    "PlatformModule",
    "DashboardModule",
    "CustomerManagementModule",
    "LicenseManagerModule",
    "RemoteConfigurationModule",
    "MonitoringModule",
    "ServerStatusModule",
    "UpdateManagerModule",
    "ALL_MODULES",
]

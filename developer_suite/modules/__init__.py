"""The platform modules, implementations of :class:`~developer_suite.modules.base.PlatformModule`.

Customer Management (Phase 3), Subscription Manager (the server-managed
replacement for the retired file-based licensing system), Monitoring
and Server Status (Phase 10), Update Manager (Phase 14), and Reporting
& Analytics (Phase 15) now have real business logic; Remote
Configuration and (Phase 12) Settings remain "coming soon"
placeholders until their own approved phases. The Dashboard (Phase 10)
is first in :data:`ALL_MODULES`, making it the main window's default
landing page.

:data:`ALL_MODULES`' order is Phase 12's requested navigation grouping,
extended by later phases in place — Dashboard, Customers, Subscriptions,
Remote Configuration, Monitoring, Updates, Reporting, Server, Settings
— the same order :mod:`developer_suite.ui.navigation` renders top to
bottom.
"""

from __future__ import annotations

from developer_suite.modules.base import PlatformModule
from developer_suite.modules.customer_management import CustomerManagementModule
from developer_suite.modules.dashboard import DashboardModule
from developer_suite.modules.device_diagnostics import DeviceDiagnosticsModule
from developer_suite.modules.monitoring import MonitoringModule
from developer_suite.modules.remote_configuration import RemoteConfigurationModule
from developer_suite.modules.reporting import ReportingModule
from developer_suite.modules.server_status import ServerStatusModule
from developer_suite.modules.settings import SettingsModule
from developer_suite.modules.subscription_manager import SubscriptionManagerModule
from developer_suite.modules.update_manager import UpdateManagerModule

ALL_MODULES: tuple[type[PlatformModule], ...] = (
    DashboardModule,
    CustomerManagementModule,
    SubscriptionManagerModule,
    RemoteConfigurationModule,
    MonitoringModule,
    UpdateManagerModule,
    ReportingModule,
    ServerStatusModule,
    DeviceDiagnosticsModule,
    SettingsModule,
)
"""Every platform module, in navigation display order."""

__all__ = [
    "PlatformModule",
    "DashboardModule",
    "CustomerManagementModule",
    "SubscriptionManagerModule",
    "RemoteConfigurationModule",
    "MonitoringModule",
    "UpdateManagerModule",
    "ReportingModule",
    "ServerStatusModule",
    "DeviceDiagnosticsModule",
    "SettingsModule",
    "ALL_MODULES",
]

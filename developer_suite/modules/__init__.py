"""The five platform modules, implementations of :class:`~developer_suite.modules.base.PlatformModule`.

Customer Management (Phase 3) and License Manager (Phase 4) now have
real business logic; Remote Configuration, Monitoring, and Update
Manager remain "coming soon" placeholders until their own approved
phases.
"""

from __future__ import annotations

from developer_suite.modules.base import PlatformModule
from developer_suite.modules.customer_management import CustomerManagementModule
from developer_suite.modules.license_manager import LicenseManagerModule
from developer_suite.modules.monitoring import MonitoringModule
from developer_suite.modules.remote_configuration import RemoteConfigurationModule
from developer_suite.modules.update_manager import UpdateManagerModule

ALL_MODULES: tuple[type[PlatformModule], ...] = (
    CustomerManagementModule,
    LicenseManagerModule,
    RemoteConfigurationModule,
    MonitoringModule,
    UpdateManagerModule,
)
"""Every platform module, in navigation display order."""

__all__ = [
    "PlatformModule",
    "CustomerManagementModule",
    "LicenseManagerModule",
    "RemoteConfigurationModule",
    "MonitoringModule",
    "UpdateManagerModule",
    "ALL_MODULES",
]

"""The five platform modules, as placeholder implementations of :class:`~developer_suite.modules.base.PlatformModule`.

Every module here is empty in Phase 2 — it registers itself with the
navigation sidebar and shows a "coming soon" placeholder page, nothing
more. Business logic for each arrives in later phases: Customer
Management and License Manager in Phase 3, Remote Configuration in
Phase 4, Monitoring alongside the sync layer, Update Manager once that
layer exists.
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

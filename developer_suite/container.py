"""Minimal dependency-injection container for the Developer Suite.

Wires configuration, the database, and the platform modules together
in one place, so :mod:`developer_suite.ui.main_window` depends only on
this container rather than constructing (or reaching for global
singletons of) each piece itself. A later phase extends
:meth:`ServiceContainer._build_modules` to pass real services into each
module's constructor once those services exist — the container is the
one place that wiring changes, not the UI layer.
"""

from __future__ import annotations

from typing import Callable

from database.database import Database
from developer_suite.admin.auth_client import AdminAuthClient
from developer_suite.admin.client import AdminApiClient
from developer_suite.admin.session_manager import AdminSessionManager
from developer_suite.config import DeveloperSuiteConfig
from developer_suite.modules import (
    ALL_MODULES,
    CustomerManagementModule,
    DashboardModule,
    LicenseManagerModule,
    MonitoringModule,
    PlatformModule,
    RemoteConfigurationModule,
    ServerStatusModule,
    UpdateManagerModule,
)
from developer_suite.services.configuration_publish_service import ConfigurationPublishService
from developer_suite.services.configuration_service import ConfigurationService
from developer_suite.services.customer_group_service import CustomerGroupService
from developer_suite.services.customer_service import CustomerService
from developer_suite.services.dashboard_refresh_service import DashboardRefreshService
from developer_suite.services.dashboard_service import DashboardService
from developer_suite.services.license_service import LicenseService
from developer_suite.services.update_manager_service import UpdateManagerService
from developer_suite.sync.coordinator import SyncCoordinator
from developer_suite.sync.customer_sync import register_customer_sync
from developer_suite.sync.scheduler import SyncSchedulerService


class ServiceContainer:
    """Holds every shared dependency the Developer Suite's UI layer needs.

    Attributes:
        config: This application's configuration.
        database: This application's own database.
        sync_coordinator: The generic push/pull engine against the
            Attendance Server (see
            :mod:`developer_suite.sync.coordinator`), with every
            currently-integrated entity's applier already registered
            (Phase 8: customers only).
        sync_scheduler: Drives :attr:`sync_coordinator` automatically
            on a timer (see :mod:`developer_suite.sync.scheduler`).
            Constructed here but not started — the composition root
            (:mod:`developer_suite.main`) calls :meth:`~developer_suite.sync.scheduler.SyncSchedulerService.start`
            once the rest of startup has succeeded, and
            :meth:`~developer_suite.sync.scheduler.SyncSchedulerService.shutdown`
            before the application exits, mirroring exactly how the
            Attendance Client's own ``main.py`` drives
            :class:`~services.scheduler_service.SchedulerService`.
        admin_auth_client: Talks to the Attendance Server's
            ``/api/v1/auth/*`` endpoints (login, refresh, logout,
            password change) — see :mod:`developer_suite.admin.auth_client`.
        admin_session_manager: Owns the current admin session's state
            (Phase 11 — see :mod:`developer_suite.admin.session_manager`);
            the concrete :class:`~developer_suite.admin.token_provider.AdminTokenProvider`
            implementation, replacing Phase 10's temporary
            ``ConfiguredAdminTokenProvider`` bootstrap.
        admin_client: Read-only client for the Attendance Server's
            administration endpoints (registered devices, recent sync
            activity, server status) — see :mod:`developer_suite.admin.client`.
        configuration_publish_service: Publishes/compares/rolls back
            configuration bundles toward a customer's Attendance
            Client installation (Phase 13 — see
            :mod:`developer_suite.services.configuration_publish_service`).
        dashboard_service: Aggregates :attr:`customer_service`,
            :attr:`license_service`, :attr:`sync_scheduler`, and
            :attr:`admin_client` into one dashboard snapshot — see
            :mod:`developer_suite.services.dashboard_service`.
        dashboard_refresh_service: Computes :attr:`dashboard_service`
            snapshots on a background thread, on a timer (Phase 12 —
            see :mod:`developer_suite.services.dashboard_refresh_service`).
            Constructed here but not started, for the same reason
            :attr:`sync_scheduler` isn't: the composition root
            (:mod:`developer_suite.main`) starts/stops it alongside
            :attr:`sync_scheduler`. Shared by the Dashboard page and
            :class:`~developer_suite.ui.main_window.MainWindow`'s
            status bar, so both render from the same background
            refresh instead of each polling the server independently.
        customer_group_service: Create/rename/delete customer groups
            and manage their membership (Phase 14 — see
            :mod:`developer_suite.services.customer_group_service`),
            used only to populate the Update Manager's "customer
            group" targeting picker.
        update_manager_service: Create, sign/upload, target, publish,
            schedule, disable, and roll back software updates (Phase
            14 — see
            :mod:`developer_suite.services.update_manager_service`).
    """

    def __init__(self, config: DeveloperSuiteConfig, database: Database) -> None:
        """Create a container, construct every service, and every platform module.

        Args:
            config: This application's configuration.
            database: This application's own, already-initialized
                database (see
                :func:`developer_suite.database.bootstrap.build_database`).
        """
        self.config = config
        self.database = database
        self.customer_service = CustomerService(database)
        self.customer_group_service = CustomerGroupService(database)
        self.license_service = LicenseService(
            database, private_key_path=config.licensing_private_key_path
        )
        self.configuration_service = ConfigurationService(database)
        self.configuration_publish_service = ConfigurationPublishService(database)
        self.sync_coordinator = SyncCoordinator(database, config)
        register_customer_sync(self.sync_coordinator)
        self.sync_scheduler = SyncSchedulerService(self.sync_coordinator, config)
        self.admin_auth_client = AdminAuthClient(config.attendance_server_url)
        self.admin_session_manager = AdminSessionManager(database, self.admin_auth_client)
        self.admin_client = AdminApiClient(config.attendance_server_url, self.admin_session_manager)
        self.update_manager_service = UpdateManagerService(
            self.admin_client, private_key_path=config.update_signing_private_key_path
        )
        self.dashboard_service = DashboardService(
            self.customer_service,
            self.license_service,
            self.sync_scheduler,
            self.admin_client,
            config,
        )
        self.dashboard_refresh_service = DashboardRefreshService(self.dashboard_service)
        self._modules: dict[str, PlatformModule] = self._build_modules()

    def _module_factories(self) -> dict[type[PlatformModule], Callable[[], PlatformModule]]:
        """Map each module class needing real dependencies to how to build it.

        Any module class in :data:`~developer_suite.modules.ALL_MODULES`
        not listed here is constructed with no arguments (see
        :meth:`_build_modules`) — this is the one place a later phase
        adds an entry when it gives another module a real service
        dependency, without touching
        :mod:`developer_suite.ui.main_window` or
        :meth:`modules`/:meth:`get_module`.
        """
        return {
            DashboardModule: lambda: DashboardModule(
                self.dashboard_refresh_service, self.customer_service, self.license_service
            ),
            CustomerManagementModule: lambda: CustomerManagementModule(
                self.customer_service, self.license_service, self.sync_coordinator
            ),
            LicenseManagerModule: lambda: LicenseManagerModule(
                self.license_service, self.customer_service
            ),
            RemoteConfigurationModule: lambda: RemoteConfigurationModule(
                self.configuration_service,
                self.configuration_publish_service,
                self.customer_service,
                self.admin_client,
                self.admin_session_manager,
            ),
            MonitoringModule: lambda: MonitoringModule(self.admin_client),
            ServerStatusModule: lambda: ServerStatusModule(self.admin_client, self.config),
            UpdateManagerModule: lambda: UpdateManagerModule(
                self.update_manager_service,
                self.customer_service,
                self.customer_group_service,
                self.admin_client,
            ),
        }

    def _build_modules(self) -> dict[str, PlatformModule]:
        """Construct every registered platform module, keyed by ``module_id``, in order."""
        factories = self._module_factories()
        modules = tuple(
            factories[module_cls]() if module_cls in factories else module_cls()
            for module_cls in ALL_MODULES
        )
        return {module.module_id: module for module in modules}

    def modules(self) -> tuple[PlatformModule, ...]:
        """Every registered platform module, in navigation display order."""
        return tuple(self._modules.values())

    def get_module(self, module_id: str) -> PlatformModule:
        """Look up a single module by its stable identifier.

        Args:
            module_id: A :attr:`~developer_suite.modules.base.PlatformModule.module_id`.

        Returns:
            The matching module.

        Raises:
            KeyError: No module with that id is registered.
        """
        return self._modules[module_id]

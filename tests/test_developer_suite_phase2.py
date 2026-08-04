"""Tests for Phase 2 of the commercial platform work (see
``docs/PLATFORM_ARCHITECTURE_GAP_ANALYSIS.md``): the Developer Suite's
foundation scaffolding.

Every test here exercises only :mod:`developer_suite`. A dedicated
class at the bottom explicitly proves this new package never touches
the Attendance Client's ``config``/``database.database`` singletons —
the concrete, automated form of "verify nothing in the customer
application changed."
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import config as attendance_config_module
import developer_suite.config as developer_suite_config_module
from database.database import Database
from developer_suite.admin.auth_client import AdminAuthClient
from developer_suite.admin.client import AdminApiClient
from developer_suite.admin.session_manager import AdminSessionManager
from developer_suite.config import DeveloperSuiteConfig, get_developer_suite_config
from developer_suite.container import ServiceContainer
from developer_suite.database.base import Base as DeveloperSuiteBase
from developer_suite.database.bootstrap import build_database
from developer_suite.modules import (
    ALL_MODULES,
    CustomerManagementModule,
    DashboardModule,
    LicenseManagerModule,
    MonitoringModule,
    RemoteConfigurationModule,
    ReportingModule,
    ServerStatusModule,
    UpdateManagerModule,
)
from developer_suite.modules.base import PlatformModule
from developer_suite.services.configuration_publish_service import ConfigurationPublishService
from developer_suite.services.configuration_service import ConfigurationService
from developer_suite.services.customer_group_service import CustomerGroupService
from developer_suite.services.customer_service import CustomerService
from developer_suite.services.dashboard_refresh_service import DashboardRefreshService
from developer_suite.services.dashboard_service import DashboardService
from developer_suite.services.license_service import LicenseService
from developer_suite.services.reporting_service import ReportingService
from developer_suite.services.update_manager_service import UpdateManagerService
from developer_suite.sync.coordinator import SyncCoordinator
from developer_suite.sync.customer_sync import register_customer_sync
from developer_suite.sync.scheduler import SyncSchedulerService
from developer_suite.ui.main_window import MainWindow
from developer_suite.ui.navigation import NavigationSidebar


class _NullAdminTokenProvider:
    """A test-only :class:`~developer_suite.admin.token_provider.AdminTokenProvider`
    that never has a token.

    These module-construction tests only need *some* object satisfying
    the abstraction — never an actual authenticated call — so a bare
    stub is enough; the real implementation is
    :class:`~developer_suite.admin.session_manager.AdminSessionManager`
    (Phase 11), exercised in its own test file.
    """

    def get_token(self) -> str | None:
        return None


def _construct_module(
    module_cls: type[PlatformModule], database: Database, config: DeveloperSuiteConfig
) -> PlatformModule:
    """Build any registered module class, supplying the real dependency it needs.

    A single, obvious place to extend when a later phase gives another
    module a real dependency — mirroring
    :meth:`developer_suite.container.ServiceContainer._module_factories`.
    """
    if module_cls is DashboardModule:
        customer_service = CustomerService(database)
        license_service = LicenseService(database, private_key_path=Path("/nonexistent/key.pem"))
        coordinator = SyncCoordinator(database, config)
        register_customer_sync(coordinator)
        scheduler = SyncSchedulerService(coordinator, config)
        admin_client = AdminApiClient(config.attendance_server_url, _NullAdminTokenProvider())
        dashboard_service = DashboardService(customer_service, license_service, scheduler, admin_client, config)
        refresh_service = DashboardRefreshService(dashboard_service)
        return DashboardModule(refresh_service, customer_service, license_service)
    if module_cls is CustomerManagementModule:
        customer_service = CustomerService(database)
        license_service = LicenseService(database, private_key_path=Path("/nonexistent/key.pem"))
        coordinator = SyncCoordinator(database, config)
        register_customer_sync(coordinator)
        return CustomerManagementModule(customer_service, license_service, coordinator)
    if module_cls is LicenseManagerModule:
        customer_service = CustomerService(database)
        license_service = LicenseService(database, private_key_path=Path("/nonexistent/key.pem"))
        return LicenseManagerModule(license_service, customer_service)
    if module_cls is RemoteConfigurationModule:
        customer_service = CustomerService(database)
        admin_client = AdminApiClient(config.attendance_server_url, _NullAdminTokenProvider())
        admin_session_manager = AdminSessionManager(
            database, AdminAuthClient(config.attendance_server_url)
        )
        return RemoteConfigurationModule(
            ConfigurationService(database),
            ConfigurationPublishService(database),
            customer_service,
            admin_client,
            admin_session_manager,
        )
    if module_cls is MonitoringModule:
        admin_client = AdminApiClient(config.attendance_server_url, _NullAdminTokenProvider())
        return MonitoringModule(admin_client)
    if module_cls is ServerStatusModule:
        admin_client = AdminApiClient(config.attendance_server_url, _NullAdminTokenProvider())
        return ServerStatusModule(admin_client, config)
    if module_cls is UpdateManagerModule:
        customer_service = CustomerService(database)
        customer_group_service = CustomerGroupService(database)
        admin_client = AdminApiClient(config.attendance_server_url, _NullAdminTokenProvider())
        update_manager_service = UpdateManagerService(
            admin_client, private_key_path=config.update_signing_private_key_path
        )
        return UpdateManagerModule(update_manager_service, customer_service, customer_group_service, admin_client)
    if module_cls is ReportingModule:
        customer_service = CustomerService(database)
        license_service = LicenseService(database, private_key_path=Path("/nonexistent/key.pem"))
        configuration_publish_service = ConfigurationPublishService(database)
        coordinator = SyncCoordinator(database, config)
        register_customer_sync(coordinator)
        scheduler = SyncSchedulerService(coordinator, config)
        admin_client = AdminApiClient(config.attendance_server_url, _NullAdminTokenProvider())
        dashboard_service = DashboardService(customer_service, license_service, scheduler, admin_client, config)
        reporting_service = ReportingService(
            customer_service, license_service, configuration_publish_service, dashboard_service, admin_client
        )
        return ReportingModule(reporting_service, config)
    return module_cls()


@pytest.fixture
def dev_suite_config(tmp_path, monkeypatch) -> DeveloperSuiteConfig:
    """A fresh :class:`DeveloperSuiteConfig` pointed at an isolated tmp database."""
    monkeypatch.setenv("DEV_SUITE_DB_SQLITE_PATH", str(tmp_path / "developer_suite_test.db"))
    developer_suite_config_module._config_instance = None
    yield get_developer_suite_config()
    developer_suite_config_module._config_instance = None


@pytest.fixture
def dev_suite_database(dev_suite_config):
    """A fresh, initialized Developer Suite database for one test."""
    database = build_database(dev_suite_config)
    yield database
    database.dispose()


class TestConfig:
    def test_load_builds_independent_instance(self, dev_suite_config: DeveloperSuiteConfig) -> None:
        assert dev_suite_config.app_name == "Developer Suite"

    def test_database_path_is_separate_from_attendance_client(
        self, dev_suite_config: DeveloperSuiteConfig, tmp_path
    ) -> None:
        assert "developer_suite" in str(dev_suite_config.database.sqlite_path).lower()

    def test_database_name_is_developer_suite(self, dev_suite_config: DeveloperSuiteConfig) -> None:
        assert dev_suite_config.database.database_name == "developer_suite"

    def test_paths_are_created(self, dev_suite_config: DeveloperSuiteConfig) -> None:
        assert dev_suite_config.paths.data_dir.exists()
        assert dev_suite_config.paths.logs_dir.exists()
        assert dev_suite_config.paths.backups_dir.exists()

    def test_singleton_returns_same_instance(self, dev_suite_config: DeveloperSuiteConfig) -> None:
        assert get_developer_suite_config() is dev_suite_config


class TestDatabaseBootstrap:
    def test_build_database_returns_connected_database(self, dev_suite_database: Database) -> None:
        assert dev_suite_database.check_connection() is True

    def test_schema_includes_registered_models(self, dev_suite_database: Database) -> None:
        # Empty as of Phase 2; Phase 3 registered the first model
        # (Customer) against DeveloperSuiteBase -- this now checks for
        # its table rather than asserting a specific historical count.
        assert "customers" in DeveloperSuiteBase.metadata.tables

    def test_database_file_is_created_at_configured_path(
        self, dev_suite_config: DeveloperSuiteConfig, dev_suite_database: Database
    ) -> None:
        assert dev_suite_config.database.sqlite_path.exists()


class TestModuleInterface:
    @pytest.mark.parametrize("module_cls", ALL_MODULES)
    def test_module_satisfies_platform_module_interface(
        self, module_cls: type[PlatformModule], dev_suite_database: Database, dev_suite_config: DeveloperSuiteConfig
    ) -> None:
        module = _construct_module(module_cls, dev_suite_database, dev_suite_config)
        assert isinstance(module, PlatformModule)
        assert isinstance(module.module_id, str) and module.module_id
        assert isinstance(module.display_name_ar, str) and module.display_name_ar
        assert isinstance(module.display_name_en, str) and module.display_name_en

    def test_module_ids_are_unique(
        self, dev_suite_database: Database, dev_suite_config: DeveloperSuiteConfig
    ) -> None:
        module_ids = [
            _construct_module(module_cls, dev_suite_database, dev_suite_config).module_id
            for module_cls in ALL_MODULES
        ]
        assert len(module_ids) == len(set(module_ids))

    def test_all_required_modules_are_registered(
        self, dev_suite_database: Database, dev_suite_config: DeveloperSuiteConfig
    ) -> None:
        module_ids = {
            _construct_module(module_cls, dev_suite_database, dev_suite_config).module_id
            for module_cls in ALL_MODULES
        }
        assert module_ids == {
            "dashboard",
            "customer_management",
            "license_manager",
            "remote_configuration",
            "monitoring",
            "server_status",
            "update_manager",
            "reporting",
            "settings",
        }

    @pytest.mark.parametrize("module_cls", ALL_MODULES)
    def test_build_page_returns_a_widget(
        self,
        module_cls: type[PlatformModule],
        dev_suite_database: Database,
        dev_suite_config: DeveloperSuiteConfig,
        qapp,
    ) -> None:
        from PySide6.QtWidgets import QWidget

        module = _construct_module(module_cls, dev_suite_database, dev_suite_config)
        page = module.build_page()
        assert isinstance(page, QWidget)


class TestServiceContainer:
    def test_registers_every_module(self, dev_suite_config, dev_suite_database) -> None:
        container = ServiceContainer(config=dev_suite_config, database=dev_suite_database)
        assert len(container.modules()) == len(ALL_MODULES)

    def test_get_module_returns_the_right_instance(self, dev_suite_config, dev_suite_database) -> None:
        container = ServiceContainer(config=dev_suite_config, database=dev_suite_database)
        module = container.get_module("license_manager")
        assert module.module_id == "license_manager"

    def test_get_module_raises_for_unknown_id(self, dev_suite_config, dev_suite_database) -> None:
        container = ServiceContainer(config=dev_suite_config, database=dev_suite_database)
        with pytest.raises(KeyError):
            container.get_module("does_not_exist")


class TestNavigationSidebar:
    def test_emits_module_selected_on_click(self, qapp) -> None:
        sidebar = NavigationSidebar([("a", "الأول"), ("b", "الثاني")])
        received: list[str] = []
        sidebar.module_selected.connect(received.append)

        buttons = sidebar.findChildren(type(sidebar._button_group.buttons()[0]))
        buttons[1].click()

        assert received == ["b"]


class TestMainWindow:
    def test_constructs_with_all_modules_in_page_stack(
        self, dev_suite_config, dev_suite_database, qapp
    ) -> None:
        container = ServiceContainer(config=dev_suite_config, database=dev_suite_database)
        window = MainWindow(container)
        assert window.page_stack.count() == len(ALL_MODULES)

    def test_show_module_switches_the_current_page(
        self, dev_suite_config, dev_suite_database, qapp
    ) -> None:
        container = ServiceContainer(config=dev_suite_config, database=dev_suite_database)
        window = MainWindow(container)

        window.show_module("monitoring")
        assert window.page_stack.currentIndex() == window._page_index_by_module_id["monitoring"]

    def test_show_module_raises_for_unknown_id(self, dev_suite_config, dev_suite_database, qapp) -> None:
        container = ServiceContainer(config=dev_suite_config, database=dev_suite_database)
        window = MainWindow(container)
        with pytest.raises(KeyError):
            window.show_module("does_not_exist")


class TestZeroImpactOnAttendanceClient:
    """Automated proof that this whole package never touches the customer app."""

    def test_importing_developer_suite_does_not_create_attendance_config_singleton(self) -> None:
        assert attendance_config_module._config_instance is None

    def test_developer_suite_database_is_a_distinct_instance_from_attendance_clients(
        self, dev_suite_database: Database
    ) -> None:
        from database.database import _database_instance

        assert dev_suite_database is not _database_instance

    def test_developer_suite_config_singleton_is_independent_of_attendance_configs(
        self, dev_suite_config: DeveloperSuiteConfig
    ) -> None:
        assert developer_suite_config_module._config_instance is not attendance_config_module._config_instance

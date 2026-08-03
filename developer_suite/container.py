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
from developer_suite.config import DeveloperSuiteConfig
from developer_suite.modules import (
    ALL_MODULES,
    CustomerManagementModule,
    LicenseManagerModule,
    PlatformModule,
    RemoteConfigurationModule,
)
from developer_suite.services.configuration_service import ConfigurationService
from developer_suite.services.customer_service import CustomerService
from developer_suite.services.license_service import LicenseService


class ServiceContainer:
    """Holds every shared dependency the Developer Suite's UI layer needs.

    Attributes:
        config: This application's configuration.
        database: This application's own database.
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
        self.license_service = LicenseService(
            database, private_key_path=config.licensing_private_key_path
        )
        self.configuration_service = ConfigurationService(database)
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
            CustomerManagementModule: lambda: CustomerManagementModule(self.customer_service),
            LicenseManagerModule: lambda: LicenseManagerModule(
                self.license_service, self.customer_service
            ),
            RemoteConfigurationModule: lambda: RemoteConfigurationModule(self.configuration_service),
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

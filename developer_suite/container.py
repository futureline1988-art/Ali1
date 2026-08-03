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

from database.database import Database
from developer_suite.config import DeveloperSuiteConfig
from developer_suite.modules import ALL_MODULES, PlatformModule


class ServiceContainer:
    """Holds every shared dependency the Developer Suite's UI layer needs.

    Attributes:
        config: This application's configuration.
        database: This application's own database.
    """

    def __init__(self, config: DeveloperSuiteConfig, database: Database) -> None:
        """Create a container and construct every platform module.

        Args:
            config: This application's configuration.
            database: This application's own, already-initialized
                database (see
                :func:`developer_suite.database.bootstrap.build_database`).
        """
        self.config = config
        self.database = database
        self._modules: dict[str, PlatformModule] = self._build_modules()

    def _build_modules(self) -> dict[str, PlatformModule]:
        """Construct every registered platform module, keyed by ``module_id``.

        Every module in Phase 2 has a no-argument constructor (see
        :mod:`developer_suite.modules` — none has any real dependency
        yet). A later phase that gives a module a real service
        dependency changes only this method, not any caller of
        :meth:`modules`/:meth:`get_module`.
        """
        modules = tuple(module_cls() for module_cls in ALL_MODULES)
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

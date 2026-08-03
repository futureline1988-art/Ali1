"""Minimal dependency-injection container for the Platform Server.

Mirrors ``developer_suite/container.py``'s role: one place holding
this server's configuration and database, so route handlers depend on
this container (via ``request.app.state.container``, wired in
:func:`server.api.app.create_app`) rather than reaching for any
process-wide singleton. Holds no services yet — Phase 6 has none to
hold (see ``server/services/__init__.py``); a later phase extends
:class:`ServiceContainer` the same way
:class:`developer_suite.container.ServiceContainer` already grew from
holding nothing to holding ``customer_service``, ``license_service``,
and ``configuration_service`` one phase at a time.
"""

from __future__ import annotations

from database.database import Database
from server.config import ServerConfig


class ServiceContainer:
    """Holds every shared dependency the Platform Server's API layer needs.

    Attributes:
        config: This server's configuration.
        database: This server's own database.
    """

    def __init__(self, config: ServerConfig, database: Database) -> None:
        """Create a container.

        Args:
            config: This server's configuration.
            database: This server's own, already-initialized database
                (see :func:`server.database.bootstrap.build_database`).
        """
        self.config = config
        self.database = database

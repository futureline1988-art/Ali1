"""Minimal dependency-injection container for the Attendance Server.

Mirrors ``developer_suite/container.py``'s role: one place holding
this server's configuration, database, and services, so route handlers
depend on this container (via ``request.app.state.container``, wired
in :func:`server.api.app.create_app`) rather than reaching for any
process-wide singleton. Phase 7 is the first phase with services to
hold — grown one phase at a time, the same way
:class:`developer_suite.container.ServiceContainer` grew from holding
nothing to holding ``customer_service``, ``license_service``, and
``configuration_service``.
"""

from __future__ import annotations

from database.database import Database
from server.config import ServerConfig
from server.services.admin_auth_service import AdminAuthService
from server.services.device_service import DeviceService
from server.services.sync_service import SyncService
from server.services.update_service import UpdateService


class ServiceContainer:
    """Holds every shared dependency the Attendance Server's API layer needs.

    Attributes:
        config: This server's configuration.
        database: This server's own database.
        device_service: Registers and authenticates devices.
        sync_service: Push/pull/conflict-resolution against the
            generic change ledger.
        admin_auth_service: Login/refresh/logout/password-change-and
            -reset for admin accounts (Phase 11) — entirely unrelated
            to :attr:`device_service`/:attr:`sync_service`'s device
            -credential auth, see
            :mod:`server.services.admin_auth_service`'s own docstring.
        update_service: Software update version/package/targeting/
            rollback management and device status reporting (Phase
            14) — see :mod:`server.services.update_service`.
    """

    def __init__(self, config: ServerConfig, database: Database) -> None:
        """Create a container, constructing every service.

        Args:
            config: This server's configuration.
            database: This server's own, already-initialized database
                (see :func:`server.database.bootstrap.build_database`).
        """
        self.config = config
        self.database = database
        self.device_service = DeviceService(database, config=config)
        self.sync_service = SyncService(database)
        self.admin_auth_service = AdminAuthService(database, config=config)
        self.update_service = UpdateService(database, config=config)

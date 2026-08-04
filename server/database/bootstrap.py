"""Database bootstrap for the Attendance Server.

Reuses :class:`~database.database.Database` directly and unmodified —
it already accepts an injectable :class:`~config.DatabaseConfig` and
exposes a public ``.engine`` — so a second, fully independent database
instance needs no change to that shared class, exactly as
:func:`developer_suite.database.bootstrap.build_database` already
established.

This module does *not* seed an initial admin account. An earlier
version of this file did, via
``ATTENDANCE_SERVER_BOOTSTRAP_ADMIN_USERNAME``/``ATTENDANCE_SERVER_BOOTSTRAP_ADMIN_PASSWORD``
environment variables — removed deliberately: an env-var-driven
default credential is still a hidden username/password living in the
project's configuration surface, just one step removed from being
hardcoded in source. The very first admin account is now created
interactively, through the same UI everyone else logs in through — see
:meth:`~server.services.admin_auth_service.AdminAuthService.bootstrap_first_admin`
and ``developer_suite/ui/first_run_setup_window.py``.
"""

from __future__ import annotations

from sqlalchemy import func, select

from database.database import Database
from server.config import ServerConfig
from server.database.base import Base
from server.models.sync import SyncSequence


def build_database(config: ServerConfig) -> Database:
    """Build, connect, and create the schema for this server's own database.

    Args:
        config: This server's configuration.

    Returns:
        A connected :class:`~database.database.Database`, with every
        registered :class:`~server.database.base.ServerBaseModel`
        subclass's table created and the
        :class:`~server.models.sync.SyncSequence` lock row seeded.
        Whether any :class:`~server.models.admin_account.AdminAccount`
        exists yet is left entirely to
        :meth:`~server.services.admin_auth_service.AdminAuthService.needs_initial_setup` —
        this function does not create or check for one.
    """
    database = Database(database_config=config.database)
    database.check_connection()
    Base.metadata.create_all(bind=database.engine)
    _ensure_sync_sequence_seeded(database)
    return database


def _ensure_sync_sequence_seeded(database: Database) -> None:
    """Create the single :class:`~server.models.sync.SyncSequence` row if it is missing.

    Idempotent — safe to call on every process startup, including
    against an already-provisioned database. See that model's own
    docstring for what the row is for; nothing that locks it works
    until it exists.
    """
    with database.session_scope() as session:
        row_count = session.execute(select(func.count()).select_from(SyncSequence)).scalar_one()
        if row_count == 0:
            session.add(SyncSequence())

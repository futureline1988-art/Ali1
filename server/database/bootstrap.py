"""Database bootstrap for the Attendance Server.

Reuses :class:`~database.database.Database` directly and unmodified —
it already accepts an injectable :class:`~config.DatabaseConfig` and
exposes a public ``.engine`` — so a second, fully independent database
instance needs no change to that shared class, exactly as
:func:`developer_suite.database.bootstrap.build_database` already
established.
"""

from __future__ import annotations

import os

from sqlalchemy import func, select

from database.database import Database
from server.config import ServerConfig
from server.database.base import Base
from server.models.admin_account import AdminAccount, AdminRole
from server.models.sync import SyncSequence
from server.services.admin_auth_service import AdminAuthService, PasswordPolicyError
from utils.logger import logger


def build_database(config: ServerConfig) -> Database:
    """Build, connect, and create the schema for this server's own database.

    Args:
        config: This server's configuration.

    Returns:
        A connected :class:`~database.database.Database`, with every
        registered :class:`~server.database.base.ServerBaseModel`
        subclass's table created, the
        :class:`~server.models.sync.SyncSequence` lock row seeded, and
        (Phase 11) an initial :class:`~server.models.admin_account.AdminAccount`
        seeded if none exist yet and bootstrap credentials were
        supplied — see :func:`_ensure_bootstrap_admin_seeded`.
    """
    database = Database(database_config=config.database)
    database.check_connection()
    Base.metadata.create_all(bind=database.engine)
    _ensure_sync_sequence_seeded(database)
    _ensure_bootstrap_admin_seeded(database, config)
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


def _ensure_bootstrap_admin_seeded(database: Database, config: ServerConfig) -> None:
    """Create one initial super-admin account, if this is a brand-new deployment.

    Idempotent and safe to call on every process startup: it only ever
    acts when the ``admin_accounts`` table is completely empty *and*
    ``ATTENDANCE_SERVER_BOOTSTRAP_ADMIN_USERNAME``/``ATTENDANCE_SERVER_BOOTSTRAP_ADMIN_PASSWORD``
    are both set — a real deployment sets them once to provision the
    first login, then normal admin account management (out of scope
    for Phase 11 — see :mod:`server.services.admin_auth_service`'s
    module docstring) takes over. There is no API endpoint that creates
    accounts; this is the only account-provisioning path today.
    """
    username = os.getenv("ATTENDANCE_SERVER_BOOTSTRAP_ADMIN_USERNAME")
    password = os.getenv("ATTENDANCE_SERVER_BOOTSTRAP_ADMIN_PASSWORD")
    if not username or not password:
        return

    with database.session_scope() as session:
        existing_count = session.execute(select(func.count()).select_from(AdminAccount)).scalar_one()
    if existing_count > 0:
        return

    try:
        AdminAuthService(database, config=config).create_account(
            username=username, password=password, role=AdminRole.SUPER_ADMIN
        )
    except PasswordPolicyError as exc:
        logger.error(
            "Bootstrap admin account was not created: password fails the configured policy ({error}).",
            error=str(exc),
        )
        return

    logger.info("Bootstrap admin account {username!r} created.", username=username)

"""Database bootstrap for the Attendance Server.

Reuses :class:`~database.database.Database` directly and unmodified —
it already accepts an injectable :class:`~config.DatabaseConfig` and
exposes a public ``.engine`` — so a second, fully independent database
instance needs no change to that shared class, exactly as
:func:`developer_suite.database.bootstrap.build_database` already
established.
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

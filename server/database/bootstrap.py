"""Database bootstrap for the Attendance Server.

Reuses :class:`~database.database.Database` directly and unmodified —
it already accepts an injectable :class:`~config.DatabaseConfig` and
exposes a public ``.engine`` — so a second, fully independent database
instance needs no change to that shared class, exactly as
:func:`developer_suite.database.bootstrap.build_database` already
established.
"""

from __future__ import annotations

from database.database import Database
from server.config import ServerConfig
from server.database.base import Base


def build_database(config: ServerConfig) -> Database:
    """Build, connect, and create the schema for this server's own database.

    Args:
        config: This server's configuration.

    Returns:
        A connected :class:`~database.database.Database`, with every
        registered :class:`~server.database.base.ServerBaseModel`
        subclass's table created.
    """
    database = Database(database_config=config.database)
    database.check_connection()
    Base.metadata.create_all(bind=database.engine)
    return database

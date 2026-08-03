"""Build and initialize the Developer Suite's own database.

Reuses :class:`database.database.Database` unmodified — its
constructor already accepts an injectable
:class:`~config.DatabaseConfig`, and its ``engine`` property is already
public, so this application's own, completely independent database can
be built without any change to ``database/database.py`` and without
going through that module's process-wide :func:`database.database.get_database`
singleton (which belongs to the Attendance Client).

``Database.initialize()``/``create_all_tables()`` are deliberately not
called here: they lazily import and create tables against
``models.base.Base`` — the Attendance Client's schema, not this
application's. This module creates
:data:`developer_suite.database.base.Base`'s (currently empty) schema
directly against the engine instead.
"""

from __future__ import annotations

from database.database import Database, DatabaseConnectionError
from developer_suite.config import DeveloperSuiteConfig
from developer_suite.database.base import Base


def build_database(config: DeveloperSuiteConfig) -> Database:
    """Construct, connect, and initialize the Developer Suite's database.

    Args:
        config: The application configuration to build it from (see
            :func:`developer_suite.config.get_developer_suite_config`).

    Returns:
        A ready-to-use :class:`~database.database.Database` instance,
        connected and with this application's schema created.

    Raises:
        DatabaseConnectionError: The configured database is not
            reachable.
    """
    database = Database(database_config=config.database)
    if not database.check_connection():
        raise DatabaseConnectionError(
            f"Cannot reach the Developer Suite database "
            f"(dialect={config.database.dialect.value}, path={config.database.sqlite_path})"
        )
    Base.metadata.create_all(bind=database.engine)
    return database

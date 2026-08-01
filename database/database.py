"""SQLAlchemy engine and session lifecycle management.

This module owns the single :class:`~sqlalchemy.engine.Engine` used by the
application and exposes thread-safe, transaction-scoped access to
:class:`~sqlalchemy.orm.Session` objects through :meth:`Database.session_scope`.

It is deliberately decoupled from the ``models`` package at import time:
domain models are only imported lazily, inside :meth:`Database.create_all_tables`
and :meth:`Database.drop_all_tables`. This keeps the infrastructure layer
importable and independently testable before any ORM model exists, and
avoids import-order coupling between ``database`` and ``models``.

Typical usage from the repository layer::

    from database.database import get_database

    with get_database().session_scope() as session:
        session.add(some_entity)

Composition root (``main.py``) usage::

    from database.database import get_database

    get_database().initialize()
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, scoped_session, sessionmaker
from sqlalchemy.pool import QueuePool, StaticPool

from config import DatabaseConfig, DatabaseDialect, get_config

logger = logging.getLogger(__name__)


class DatabaseConnectionError(RuntimeError):
    """Raised when the configured database cannot be reached or initialized."""


class Database:
    """Owns the SQLAlchemy engine and hands out thread-safe sessions.

    A single instance of this class is created per process (see
    :func:`get_database`) and is safe to share across the Qt main thread
    and background worker threads (device polling, scheduled backups,
    report generation) because sessions are scoped per-thread via
    :class:`sqlalchemy.orm.scoped_session`.

    Attributes:
        config: The :class:`~config.DatabaseConfig` this instance was
            built from.
    """

    def __init__(self, database_config: DatabaseConfig | None = None) -> None:
        """Create the engine and session factory for the given configuration.

        Args:
            database_config: Database connection settings. Defaults to the
                database section of the process-wide :func:`config.get_config`.

        Raises:
            DatabaseConnectionError: If the SQLAlchemy engine cannot be
                constructed for the configured dialect and URL.
        """
        self.config: DatabaseConfig = database_config or get_config().database
        self._lock = threading.Lock()
        self._engine: Engine = self._build_engine()
        self._session_factory = sessionmaker(
            bind=self._engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        self._scoped_session: scoped_session[Session] = scoped_session(
            self._session_factory
        )
        self._register_sqlite_pragmas()
        logger.info(
            "Database engine initialized (dialect=%s)", self.config.dialect.value
        )

    # ------------------------------------------------------------------
    # Engine construction
    # ------------------------------------------------------------------
    def _build_engine(self) -> Engine:
        """Build the SQLAlchemy engine for the configured dialect.

        Returns:
            A configured :class:`~sqlalchemy.engine.Engine`.

        Raises:
            DatabaseConnectionError: If engine creation fails, e.g. due to
                an invalid connection URL or an unwritable SQLite path.
        """
        url = self.config.build_url()
        engine_kwargs: dict[str, object] = {
            "echo": self.config.echo_sql,
            "future": True,
        }

        if self.config.dialect is DatabaseDialect.SQLITE:
            engine_kwargs["connect_args"] = {"check_same_thread": False}
            if str(self.config.sqlite_path) == ":memory:":
                engine_kwargs["poolclass"] = StaticPool
            else:
                self.config.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            engine_kwargs["poolclass"] = QueuePool
            engine_kwargs["pool_size"] = self.config.pool_size
            engine_kwargs["max_overflow"] = self.config.max_overflow
            engine_kwargs["pool_recycle"] = self.config.pool_recycle_seconds
            engine_kwargs["pool_pre_ping"] = True
            engine_kwargs["connect_args"] = {
                "connect_timeout": self.config.connect_timeout_seconds
            }

        try:
            return create_engine(url, **engine_kwargs)
        except SQLAlchemyError as exc:
            raise DatabaseConnectionError(
                f"Failed to create database engine for dialect "
                f"'{self.config.dialect.value}': {exc}"
            ) from exc

    def _register_sqlite_pragmas(self) -> None:
        """Enable foreign-key enforcement and WAL journaling for SQLite.

        SQLite disables foreign-key constraint enforcement by default and
        the default journal mode does not allow concurrent readers during
        a write; both are corrected on every new DBAPI connection.
        """
        if self.config.dialect is not DatabaseDialect.SQLITE:
            return

        @event.listens_for(self._engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    # ------------------------------------------------------------------
    # Session access
    # ------------------------------------------------------------------
    @property
    def engine(self) -> Engine:
        """The underlying SQLAlchemy :class:`~sqlalchemy.engine.Engine`."""
        return self._engine

    def get_session(self) -> Session:
        """Return the current thread-local :class:`~sqlalchemy.orm.Session`.

        The caller is responsible for committing, rolling back and closing
        the session (via :meth:`remove_session`). Prefer
        :meth:`session_scope` unless manual transaction control is
        genuinely required (e.g. a long-lived background worker session).

        Returns:
            The :class:`~sqlalchemy.orm.Session` bound to the calling
            thread.
        """
        return self._scoped_session()

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        """Provide a transactional unit of work as a context manager.

        Commits on successful exit, rolls back on any exception, and
        always closes and detaches the thread-local session afterward.

        Yields:
            An active :class:`~sqlalchemy.orm.Session`.

        Raises:
            Exception: Re-raises whatever exception occurred inside the
                ``with`` block, after rolling back the transaction.
        """
        session = self._scoped_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            self._scoped_session.remove()

    def remove_session(self) -> None:
        """Dispose of the current thread's session, if one is open.

        Must be called at the end of a background worker thread's
        lifecycle to avoid leaking connections back to the pool.
        """
        self._scoped_session.remove()

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------
    def create_all_tables(self) -> None:
        """Create every table declared on the domain models' metadata.

        Imports :data:`models.base.Base` lazily so this module has no
        import-time dependency on the ``models`` package.
        """
        from models.base import Base  # noqa: PLC0415 - intentional lazy import

        with self._lock:
            Base.metadata.create_all(bind=self._engine)
        logger.info("Database schema created/verified")

    def drop_all_tables(self) -> None:
        """Drop every table declared on the domain models' metadata.

        Intended for test suites and local development resets only; must
        never be invoked against a production database.
        """
        from models.base import Base  # noqa: PLC0415 - intentional lazy import

        with self._lock:
            Base.metadata.drop_all(bind=self._engine)
        logger.warning("Database schema dropped")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def check_connection(self) -> bool:
        """Verify that the database is reachable.

        Returns:
            ``True`` if a trivial query executes successfully, ``False``
            otherwise. Failures are logged, not raised.
        """
        try:
            with self._engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except SQLAlchemyError as exc:
            logger.error("Database connection check failed: %s", exc)
            return False

    def initialize(self) -> None:
        """Verify connectivity and ensure the schema exists.

        This is the method the application composition root calls once at
        startup, before any repository or service touches the database.

        Raises:
            DatabaseConnectionError: If the database is not reachable.
        """
        if not self.check_connection():
            raise DatabaseConnectionError(
                f"Cannot reach database "
                f"(dialect={self.config.dialect.value}, "
                f"host={getattr(self.config, 'host', 'n/a')})"
            )
        self.create_all_tables()

    def dispose(self) -> None:
        """Release every pooled connection and detach all sessions.

        Should be called once, on application shutdown.
        """
        self._scoped_session.remove()
        self._engine.dispose()
        logger.info("Database engine disposed")


_database_instance: Database | None = None
_singleton_lock = threading.Lock()


def get_database() -> Database:
    """Return the process-wide :class:`Database` singleton.

    Thread-safe: concurrent first-time callers from different threads
    (e.g. UI thread and a device worker thread starting simultaneously)
    will still only construct a single :class:`Database` instance.

    Returns:
        The shared :class:`Database` instance.
    """
    global _database_instance
    if _database_instance is None:
        with _singleton_lock:
            if _database_instance is None:
                _database_instance = Database()
    return _database_instance


@contextmanager
def session_scope() -> Iterator[Session]:
    """Module-level convenience wrapper around ``get_database().session_scope()``.

    Allows repositories and services to write
    ``from database.database import session_scope`` without threading the
    :class:`Database` singleton through every constructor.

    Yields:
        An active :class:`~sqlalchemy.orm.Session`.
    """
    with get_database().session_scope() as session:
        yield session

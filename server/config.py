"""Attendance Server configuration.

Composes the pieces of :mod:`config` (the Attendance Client's config
module) that are already fully generic — :class:`~config.DatabaseConfig`,
:class:`~config.SecurityConfig`, :class:`~config.LoggingConfig`,
:class:`~config.ApiConfig`, and :class:`~config.Environment` carry zero
Attendance-specific fields — with a small set of settings genuinely
specific to this server. Nothing here modifies :mod:`config`, and
nothing here touches :func:`config.get_config`'s process-wide
singleton or :func:`developer_suite.config.get_developer_suite_config`'s:
this module builds its own, completely independent
:class:`ServerConfig` instance, exactly mirroring the pattern
:mod:`developer_suite.config` already established for the Developer
Suite.

:meth:`config.DatabaseConfig.from_env` and
:meth:`config.SecurityConfig.from_env` both read *unprefixed*
environment variables (``DB_*``, ``APP_SECRET_KEY``, ...) — reusing
them as-is here would mean this server's database connection and
signing secret silently collide with the Attendance Client's own
``DB_*``/``APP_SECRET_KEY`` variables whenever both happen to run in
the same environment (exactly the concurrent-test-suite situation this
repository's own CI runs in). ``ATTENDANCE_SERVER_DB_*`` values are therefore
resolved independently below by :func:`_load_database_config`, and the
signing secret through its own ``ATTENDANCE_SERVER_SECRET_KEY`` variable — the
*fields* of :class:`~config.DatabaseConfig`/:class:`~config.SecurityConfig`
are still fully reused, only their environment-variable *names* are
kept independent, the same tradeoff
:class:`developer_suite.config.DeveloperSuitePaths` already made for
its data directory.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

from config import ApiConfig, DatabaseConfig, DatabaseDialect, Environment, LoggingConfig, SecurityConfig


def _env_bool(name: str, default: bool) -> bool:
    """Read an environment variable as a boolean (mirrors ``config._env_bool``)."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    """Read an environment variable as an integer (mirrors ``config._env_int``)."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _resolve_data_root() -> Path:
    """Where this server's writable runtime data (SQLite fallback, logs) lives.

    Most real deployments set ``ATTENDANCE_SERVER_DATA_DIR`` explicitly
    (a container volume, a service account's chosen directory, ...),
    which always wins below regardless of how this process was
    started. Absent that, this mirrors
    ``config._resolve_data_root``/``developer_suite.config._resolve_data_root``'s
    frozen-vs-development split: a PyInstaller-frozen build of this
    server (``packaging/pyinstaller/attendance_server.spec``, for an
    operator who just wants to double-click an .exe rather than run
    ``python -m server.main``) resolves under ``%LOCALAPPDATA%\\AttendanceServer``
    so its SQLite database and logs persist across restarts instead of
    landing inside PyInstaller's own temp extraction path, which is
    wiped between runs. A non-frozen ``python -m server.main`` process
    keeps using the repository-relative ``attendance_server_data/``
    directory it always has.
    """
    env_override = os.getenv("ATTENDANCE_SERVER_DATA_DIR")
    if env_override:
        return Path(env_override)
    if getattr(sys, "frozen", False):
        local_appdata = os.getenv("LOCALAPPDATA")
        if local_appdata:
            return Path(local_appdata) / "AttendanceServer"
        return Path.home() / ".attendance_server"
    return Path(__file__).resolve().parent.parent / "attendance_server_data"


@dataclass(frozen=True)
class ServerPaths:
    """The small filesystem layout this server actually needs.

    Attributes:
        data_dir: Where a local SQLite database file lives (development
            only — a real deployment sets ``ATTENDANCE_SERVER_DB_DIALECT`` to
            ``postgresql`` and never touches this).
        logs_dir: Where log files are written.
    """

    data_dir: Path
    logs_dir: Path

    @classmethod
    def default(cls) -> "ServerPaths":
        """Build the default layout under this server's data root."""
        root = _resolve_data_root()
        return cls(data_dir=root / "data", logs_dir=root / "logs")

    def ensure_created(self) -> None:
        """Create every directory declared above if it does not exist."""
        for directory in (self.data_dir, self.logs_dir):
            directory.mkdir(parents=True, exist_ok=True)


def _load_database_config(paths: ServerPaths) -> DatabaseConfig:
    """Build this server's :class:`~config.DatabaseConfig` from ``ATTENDANCE_SERVER_DB_*`` variables.

    Mirrors :meth:`config.DatabaseConfig.from_env`'s field-by-field
    resolution exactly, but reads independently-namespaced variables so
    this server's connection settings — almost certainly PostgreSQL in
    any real deployment, per the platform design's "hundreds or
    thousands of companies" requirement — never collide with the
    Attendance Client's own ``DB_*`` variables.
    """
    dialect_raw = os.getenv("ATTENDANCE_SERVER_DB_DIALECT", DatabaseDialect.SQLITE.value)
    try:
        dialect = DatabaseDialect(dialect_raw.lower())
    except ValueError:
        dialect = DatabaseDialect.SQLITE

    default_port = {
        DatabaseDialect.POSTGRESQL: 5432,
        DatabaseDialect.MYSQL: 3306,
        DatabaseDialect.SQLITE: 0,
    }[dialect]

    return DatabaseConfig(
        dialect=dialect,
        sqlite_path=Path(
            os.getenv("ATTENDANCE_SERVER_DB_SQLITE_PATH", str(paths.data_dir / "attendance_server.db"))
        ),
        host=os.getenv("ATTENDANCE_SERVER_DB_HOST", "localhost"),
        port=_env_int("ATTENDANCE_SERVER_DB_PORT", default_port),
        username=os.getenv("ATTENDANCE_SERVER_DB_USERNAME", ""),
        password=os.getenv("ATTENDANCE_SERVER_DB_PASSWORD", ""),
        database_name=os.getenv("ATTENDANCE_SERVER_DB_NAME", "attendance_server"),
        echo_sql=_env_bool("ATTENDANCE_SERVER_DB_ECHO_SQL", False),
        pool_size=_env_int("ATTENDANCE_SERVER_DB_POOL_SIZE", 10),
        max_overflow=_env_int("ATTENDANCE_SERVER_DB_MAX_OVERFLOW", 20),
        pool_recycle_seconds=_env_int("ATTENDANCE_SERVER_DB_POOL_RECYCLE_SECONDS", 1800),
        connect_timeout_seconds=_env_int("ATTENDANCE_SERVER_DB_CONNECT_TIMEOUT_SECONDS", 10),
    )


@dataclass(frozen=True)
class ServerConfig:
    """Aggregate configuration root for the Attendance Server.

    Attributes:
        app_name: Display name used in logs and the ``/version``
            endpoint.
        app_version: This server's own version, independent of the
            Attendance Client's and Developer Suite's — all three are
            versioned and released separately.
        environment: Reuses :class:`config.Environment` directly — the
            same three-way development/testing/production split, with
            the same meaning.
        paths: This server's own, small filesystem layout (see
            :class:`ServerPaths`).
        database: Reuses :class:`config.DatabaseConfig` directly,
            resolved from independent ``ATTENDANCE_SERVER_DB_*`` variables (see
            :func:`_load_database_config`) — never the Attendance
            Client's or Developer Suite's own database.
        security: Reuses :class:`config.SecurityConfig` directly, with
            its own ``ATTENDANCE_SERVER_SECRET_KEY``-sourced signing secret
            rather than the Attendance Client's ``APP_SECRET_KEY``.
        logging: Reuses :class:`config.LoggingConfig` directly, with
            its own log file name.
        api: Reuses :class:`config.ApiConfig` directly — host, port,
            and token lifetime are exactly what this server needs too.
    """

    app_name: str = "Attendance Server"
    app_version: str = "1.1.3"
    environment: Environment = Environment.PRODUCTION

    paths: ServerPaths = field(default_factory=ServerPaths.default)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    api: ApiConfig = field(default_factory=ApiConfig)

    @classmethod
    def load(cls) -> "ServerConfig":
        """Build a fully-populated :class:`ServerConfig` from the environment.

        Mirrors :meth:`config.AppConfig.load`'s and
        :meth:`developer_suite.config.DeveloperSuiteConfig.load`'s
        shape (read environment variables, fall back to defaults,
        ensure writable directories exist) but with this server's own
        variable names so all three applications can be configured
        independently, even side by side on the same machine.

        Returns:
            A ready-to-use :class:`ServerConfig`.
        """
        env_raw = os.getenv("ATTENDANCE_SERVER_ENVIRONMENT", Environment.PRODUCTION.value)
        try:
            environment = Environment(env_raw.lower())
        except ValueError:
            environment = Environment.PRODUCTION

        paths = ServerPaths.default()
        paths.ensure_created()

        database = _load_database_config(paths)

        security = replace(
            SecurityConfig.from_env(),
            secret_key=os.getenv(
                "ATTENDANCE_SERVER_SECRET_KEY", "change-this-attendance-server-secret-key-in-production"
            ),
        )

        logging_config = LoggingConfig(
            level=os.getenv("ATTENDANCE_SERVER_LOG_LEVEL", "INFO").upper(),
            rotation=os.getenv("ATTENDANCE_SERVER_LOG_ROTATION", "10 MB"),
            retention=os.getenv("ATTENDANCE_SERVER_LOG_RETENTION", "30 days"),
            log_file_name="attendance_server.log",
        )

        api = ApiConfig(
            enabled=True,
            host=os.getenv("ATTENDANCE_SERVER_API_HOST", "0.0.0.0"),
            # 8000, not config.ApiConfig's own port field's value read some
            # other way -- deliberately the same literal default both
            # clients already carry independently: developer_suite.config's
            # DeveloperSuiteConfig.attendance_server_url and this repo's own
            # config.SyncConfig.server_url both default to
            # "http://127.0.0.1:8000". A fresh install of all three
            # applications must work with zero manual configuration, so
            # this server's own default has to be the one two independent
            # client defaults already agree on, not a separately-chosen
            # value (9000, before this fix) that quietly required everyone
            # to override something by hand.
            port=_env_int("ATTENDANCE_SERVER_API_PORT", 8000),
            token_expires_minutes=_env_int("ATTENDANCE_SERVER_API_TOKEN_EXPIRES_MINUTES", 480),
        )

        return cls(
            app_name=os.getenv("ATTENDANCE_SERVER_APP_NAME", cls.app_name),
            app_version=os.getenv("ATTENDANCE_SERVER_APP_VERSION", cls.app_version),
            environment=environment,
            paths=paths,
            database=database,
            security=security,
            logging=logging_config,
            api=api,
        )


_config_instance: ServerConfig | None = None


def get_server_config() -> ServerConfig:
    """Return the process-wide :class:`ServerConfig` singleton.

    Entirely independent of :func:`config.get_config`'s and
    :func:`developer_suite.config.get_developer_suite_config`'s
    singletons — none of the three ever interact, so running any one
    application's test suite can never leak configuration state into
    another's.

    Returns:
        The shared :class:`ServerConfig` instance.
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = ServerConfig.load()
    return _config_instance

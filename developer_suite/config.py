"""Developer Suite configuration.

Composes the pieces of :mod:`config` (the Attendance Client's config
module) that are already fully generic — :class:`~config.DatabaseConfig`,
:class:`~config.SecurityConfig`, :class:`~config.LoggingConfig`, and
:class:`~config.Environment` carry zero Attendance-specific fields —
with a small set of settings genuinely specific to this application.
Nothing here modifies :mod:`config`, and nothing here touches
:func:`config.get_config`'s process-wide singleton: this module builds
its own, completely independent :class:`DeveloperSuiteConfig` instance.

:class:`~config.PathsConfig` is deliberately *not* reused as-is: its
fields (``employee_photos_dir``, ``qrcodes_dir``, ``barcodes_dir``,
``reports_dir``, ...) are Attendance-Client-specific and meaningless
here, so reusing it would mean carrying a set of unused, misleading
fields rather than genuine reuse. :class:`DeveloperSuitePaths` below is
the small, honestly-scoped equivalent.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from config import DatabaseConfig, Environment, LoggingConfig, SecurityConfig


def _env_bool(name: str, default: bool) -> bool:
    """Read an environment variable as a boolean, accepting common truthy strings.

    A small local duplicate of ``config._env_bool`` rather than an
    import of it — that helper is module-private (leading underscore),
    the same "small, independently-parameterized duplication over a
    private cross-module import" choice this file already makes for
    ``_resolve_data_root``.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    """Read an environment variable as an integer, falling back on error."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _resolve_data_root() -> Path:
    """Where this application's writable runtime data lives.

    Mirrors ``config._resolve_data_root``'s frozen-vs-development
    logic exactly, but for this application's own AppData subfolder
    (``DeveloperSuite``, not ``AttendanceManagementSystem``) — the one
    piece of this pattern that must differ per application, which is
    why this is a few duplicated lines rather than an import from
    :mod:`config`: even shared, it would need the folder name
    parameterized, and :mod:`config` documents itself as having "no
    dependency on any other module in this project" by design, so
    routing this application's naming through it either way is not a
    clean fit.
    """
    if getattr(sys, "frozen", False):
        local_appdata = os.getenv("LOCALAPPDATA")
        if local_appdata:
            return Path(local_appdata) / "DeveloperSuite"
        return Path.home() / ".developer_suite"
    return Path(__file__).resolve().parent.parent / "developer_suite_data"


@dataclass(frozen=True)
class DeveloperSuitePaths:
    """The small filesystem layout this application actually needs.

    Attributes:
        data_dir: Where the local database file lives.
        logs_dir: Where log files are written.
        backups_dir: Where local database backups are written.
    """

    data_dir: Path
    logs_dir: Path
    backups_dir: Path

    @classmethod
    def default(cls) -> "DeveloperSuitePaths":
        """Build the default layout under this application's data root."""
        root = _resolve_data_root()
        return cls(
            data_dir=root / "data",
            logs_dir=root / "logs",
            backups_dir=root / "data" / "backups",
        )

    def ensure_created(self) -> None:
        """Create every directory declared above if it does not exist."""
        for directory in (self.data_dir, self.logs_dir, self.backups_dir):
            directory.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class DeveloperSuiteConfig:
    """Aggregate configuration root for the Developer Suite application.

    Attributes:
        app_name: Display name for window titles and window-manager
            metadata.
        app_version: This application's own version, independent of
            the Attendance Client's :attr:`config.AppConfig.app_version`
            — the two applications are versioned and released
            separately.
        environment: Reuses :class:`config.Environment` directly — the
            same three-way development/testing/production split, with
            the same meaning.
        paths: This application's own, small filesystem layout (see
            :class:`DeveloperSuitePaths`).
        database: Reuses :class:`config.DatabaseConfig` directly,
            pointed at this application's own database file — never
            the Attendance Client's.
        security: Reuses :class:`config.SecurityConfig` directly.
        logging: Reuses :class:`config.LoggingConfig` directly, with
            its own log file name.
        licensing_private_key_path: Where this application looks for
            the vendor's Ed25519 signing private key — the same key
            format :mod:`licensing.license_generator` and
            :mod:`licensing.crypto.signing` use, held only by this
            application, never the Attendance Client. A missing file
            at this path simply means license issuance isn't available
            yet (see
            :class:`~developer_suite.services.license_service.LicenseSigningKeyError`);
            nothing else in the Developer Suite depends on it.
        attendance_server_url: Base URL of the Attendance Server this
            installation synchronizes against (see
            :mod:`developer_suite.sync.client`). Purely configuration
            — knowing the URL grants no access by itself; every actual
            call still authenticates with either a device credential
            or an admin bearer token.
        sync_enabled: Whether :class:`~developer_suite.sync.scheduler.SyncSchedulerService`
            starts its periodic job at all. Defaults on, so
            synchronization runs automatically out of the box, per
            Phase 9's requirement — the escape hatch exists for the
            same reason :class:`config.DeviceConfig.auto_sync_enabled`
            exists for the Attendance Client's own scheduled job.
        sync_interval_seconds: How often the background sync job runs.
        update_signing_private_key_path: Where this application looks
            for the vendor's Ed25519 *update-signing* private key —
            the same key format
            :mod:`licensing.crypto.signing` uses, but a deliberately
            separate keypair from :attr:`licensing_private_key_path`
            (see :mod:`developer_suite.services.update_manager_service`'s
            own docstring for why licenses and update packages are
            never signed with the same key). A missing file at this
            path simply means package upload isn't available yet;
            nothing else in the Developer Suite depends on it.
    """

    app_name: str = "Developer Suite"
    app_version: str = "0.1.0"
    environment: Environment = Environment.PRODUCTION

    paths: DeveloperSuitePaths = field(default_factory=DeveloperSuitePaths.default)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    licensing_private_key_path: Path = field(
        default_factory=lambda: DeveloperSuitePaths.default().data_dir
        / "keys"
        / "license_private_key.pem"
    )
    attendance_server_url: str = "http://127.0.0.1:8000"
    sync_enabled: bool = True
    sync_interval_seconds: int = 60
    update_signing_private_key_path: Path = field(
        default_factory=lambda: DeveloperSuitePaths.default().data_dir
        / "keys"
        / "update_signing_private_key.pem"
    )

    @classmethod
    def load(cls) -> "DeveloperSuiteConfig":
        """Build a fully-populated :class:`DeveloperSuiteConfig` from the environment.

        Mirrors :meth:`config.AppConfig.load`'s shape (read environment
        variables, fall back to defaults, ensure writable directories
        exist) but with this application's own variable names so the
        two applications can be configured independently even when
        installed side by side on the same machine.

        Returns:
            A ready-to-use :class:`DeveloperSuiteConfig`.
        """
        env_raw = os.getenv("DEV_SUITE_ENVIRONMENT", Environment.PRODUCTION.value)
        try:
            environment = Environment(env_raw.lower())
        except ValueError:
            environment = Environment.PRODUCTION

        paths = DeveloperSuitePaths.default()
        paths.ensure_created()

        database = DatabaseConfig(
            sqlite_path=Path(
                os.getenv("DEV_SUITE_DB_SQLITE_PATH", str(paths.data_dir / "developer_suite.db"))
            ),
            database_name="developer_suite",
        )

        logging_config = LoggingConfig(log_file_name="developer_suite.log")

        licensing_private_key_path = Path(
            os.getenv(
                "DEV_SUITE_LICENSE_PRIVATE_KEY_PATH",
                str(paths.data_dir / "keys" / "license_private_key.pem"),
            )
        )
        update_signing_private_key_path = Path(
            os.getenv(
                "DEV_SUITE_UPDATE_SIGNING_PRIVATE_KEY_PATH",
                str(paths.data_dir / "keys" / "update_signing_private_key.pem"),
            )
        )

        return cls(
            app_name=os.getenv("DEV_SUITE_APP_NAME", cls.app_name),
            app_version=os.getenv("DEV_SUITE_APP_VERSION", cls.app_version),
            environment=environment,
            paths=paths,
            database=database,
            security=SecurityConfig.from_env(),
            logging=logging_config,
            licensing_private_key_path=licensing_private_key_path,
            attendance_server_url=os.getenv(
                "DEV_SUITE_ATTENDANCE_SERVER_URL", cls.attendance_server_url
            ),
            sync_enabled=_env_bool("DEV_SUITE_SYNC_ENABLED", cls.sync_enabled),
            sync_interval_seconds=_env_int(
                "DEV_SUITE_SYNC_INTERVAL_SECONDS", cls.sync_interval_seconds
            ),
            update_signing_private_key_path=update_signing_private_key_path,
        )


_config_instance: DeveloperSuiteConfig | None = None


def get_developer_suite_config() -> DeveloperSuiteConfig:
    """Return the process-wide :class:`DeveloperSuiteConfig` singleton.

    Entirely independent of :func:`config.get_config`'s singleton —
    the two never interact, so running either application's test suite
    can never leak configuration state into the other's.

    Returns:
        The shared :class:`DeveloperSuiteConfig` instance.
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = DeveloperSuiteConfig.load()
    return _config_instance

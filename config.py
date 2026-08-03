"""Central configuration module for the Attendance Management System.

This module is the single source of truth for every path, credential,
locale default and feature flag used across the application. It has no
dependency on any other module in this project (database, services, UI),
so it can be imported safely from anywhere without risking circular
imports.

Configuration values are resolved in the following order of precedence:

1. Explicit environment variables (``os.environ``).
2. Values loaded from a ``.env`` file at the project root, if present.
3. Hard-coded defaults defined in this module.

Usage:
    >>> from config import get_config
    >>> config = get_config()
    >>> config.database.build_url()
    'sqlite:///.../data/attendance.db'
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Final
from urllib.parse import quote_plus

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap: resolve the project root and load an optional .env file before
# any configuration value below is computed.
# ---------------------------------------------------------------------------

def _resolve_bundle_dir() -> Path:
    """Where bundled, read-only assets (fonts, icons, translations) live.

    In development this is simply this project's repository root
    directory. Frozen under PyInstaller, ``sys.frozen`` is set and bundled
    data files are unpacked to (onefile) or placed alongside (onedir)
    ``sys._MEIPASS`` — never the source tree, which does not exist in a
    packaged build at all.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def _resolve_exe_dir() -> Path:
    """The directory containing the running executable, or this file in development.

    Used only to locate an optional ``.env`` override next to the
    installed application, so an administrator can find and edit it —
    unlike :func:`_resolve_bundle_dir`, this must *not* resolve to a
    onefile build's temporary extraction directory, which is recreated
    fresh (and discarded) on every launch.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resolve_data_root() -> Path:
    """Where writable runtime data (database, logs, backups, uploads, license) lives.

    In development this equals :data:`BASE_DIR`, exactly matching this
    project's behavior before packaging was a concern — every existing
    workflow and test is unaffected. Frozen under PyInstaller it resolves
    to a proper per-user, per-machine writable location instead
    (``%LOCALAPPDATA%\\AttendanceManagementSystem`` on Windows), because
    neither alternative is safe to write to: the install directory is
    typically ``Program Files`` (not writable without elevation), and a
    onefile build's own directory is a fresh temporary extraction on
    every single launch — anything written there is silently lost the
    moment the process exits.
    """
    if getattr(sys, "frozen", False):
        local_appdata = os.getenv("LOCALAPPDATA")
        if local_appdata:
            return Path(local_appdata) / "AttendanceManagementSystem"
        return Path.home() / ".attendance_management_system"
    return BASE_DIR


BASE_DIR: Final[Path] = _resolve_bundle_dir()
"""Absolute path to the bundle root: this project's repository root
directory in development, or the PyInstaller bundle directory when frozen.
Read-only assets are resolved relative to this; see :data:`DATA_ROOT` for
where writable runtime data lives instead."""

DATA_ROOT: Final[Path] = _resolve_data_root()
"""Absolute path to the writable data root — equals :data:`BASE_DIR` in
development, and a per-user AppData location when running as a packaged
executable. See :func:`_resolve_data_root` for why the two must differ."""

load_dotenv(_resolve_exe_dir() / ".env")


class Environment(str, Enum):
    """Runtime environment the application is executing in."""

    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


class DatabaseDialect(str, Enum):
    """Database backends supported out of the box."""

    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"


class Theme(str, Enum):
    """Available visual themes for the desktop UI."""

    LIGHT = "light"
    DARK = "dark"


class Language(str, Enum):
    """Languages supported by the application UI."""

    ARABIC = "ar"
    ENGLISH = "en"


def _env_bool(name: str, default: bool) -> bool:
    """Read an environment variable as a boolean.

    Accepts common truthy strings (``"1"``, ``"true"``, ``"yes"``, ``"on"``)
    case-insensitively; anything else evaluates to ``False``.

    Args:
        name: Environment variable name to read.
        default: Value returned when the variable is not set.

    Returns:
        The parsed boolean value.
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


@dataclass(frozen=True)
class PathsConfig:
    """Filesystem layout used by the application.

    All directories are created eagerly on first access via
    :meth:`ensure_created` so the rest of the application can assume they
    already exist.
    """

    base_dir: Path = BASE_DIR
    data_dir: Path = DATA_ROOT / "data"
    logs_dir: Path = DATA_ROOT / "logs"
    backups_dir: Path = DATA_ROOT / "data" / "backups"
    uploads_dir: Path = DATA_ROOT / "data" / "uploads"
    employee_photos_dir: Path = DATA_ROOT / "data" / "uploads" / "employees"
    qrcodes_dir: Path = DATA_ROOT / "data" / "uploads" / "qrcodes"
    barcodes_dir: Path = DATA_ROOT / "data" / "uploads" / "barcodes"
    reports_dir: Path = DATA_ROOT / "data" / "reports"
    assets_dir: Path = BASE_DIR / "assets"
    icons_dir: Path = BASE_DIR / "assets" / "icons"
    images_dir: Path = BASE_DIR / "assets" / "images"
    themes_dir: Path = BASE_DIR / "assets" / "themes"
    translations_dir: Path = BASE_DIR / "assets" / "translations"

    def ensure_created(self) -> None:
        """Create every writable runtime directory if it does not exist.

        Static asset directories (``assets/*``) are expected to be shipped
        with the application and are intentionally not created here.
        """
        runtime_dirs = (
            self.data_dir,
            self.logs_dir,
            self.backups_dir,
            self.uploads_dir,
            self.employee_photos_dir,
            self.qrcodes_dir,
            self.barcodes_dir,
            self.reports_dir,
        )
        for directory in runtime_dirs:
            directory.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class DatabaseConfig:
    """Database connection settings with multi-backend support.

    Defaults to a local SQLite file so the application runs out of the
    box with zero external setup. Switching to PostgreSQL or MySQL only
    requires setting the ``DB_DIALECT`` (and related ``DB_*``) environment
    variables — no code changes are needed anywhere else in the project.
    """

    dialect: DatabaseDialect = DatabaseDialect.SQLITE
    sqlite_path: Path = DATA_ROOT / "data" / "attendance.db"
    host: str = "localhost"
    port: int = 5432
    username: str = ""
    password: str = ""
    database_name: str = "attendance_system"
    echo_sql: bool = False
    pool_size: int = 10
    max_overflow: int = 20
    pool_recycle_seconds: int = 1800
    connect_timeout_seconds: int = 10

    def build_url(self) -> str:
        """Build a SQLAlchemy connection URL for the configured dialect.

        Returns:
            A connection string ready to be passed to
            ``sqlalchemy.create_engine``.

        Raises:
            ValueError: If ``dialect`` is not one of the supported
                :class:`DatabaseDialect` values.
        """
        if self.dialect is DatabaseDialect.SQLITE:
            return f"sqlite:///{self.sqlite_path.as_posix()}"

        if self.dialect is DatabaseDialect.POSTGRESQL:
            user = quote_plus(self.username)
            pwd = quote_plus(self.password)
            return (
                f"postgresql+psycopg2://{user}:{pwd}"
                f"@{self.host}:{self.port}/{self.database_name}"
            )

        if self.dialect is DatabaseDialect.MYSQL:
            user = quote_plus(self.username)
            pwd = quote_plus(self.password)
            return (
                f"mysql+pymysql://{user}:{pwd}"
                f"@{self.host}:{self.port}/{self.database_name}?charset=utf8mb4"
            )

        raise ValueError(f"Unsupported database dialect: {self.dialect}")

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        """Build a :class:`DatabaseConfig` from environment variables."""
        dialect_raw = os.getenv("DB_DIALECT", DatabaseDialect.SQLITE.value)
        try:
            dialect = DatabaseDialect(dialect_raw.lower())
        except ValueError:
            dialect = DatabaseDialect.SQLITE

        default_port = {
            DatabaseDialect.POSTGRESQL: 5432,
            DatabaseDialect.MYSQL: 3306,
            DatabaseDialect.SQLITE: 0,
        }[dialect]

        return cls(
            dialect=dialect,
            sqlite_path=Path(
                os.getenv("DB_SQLITE_PATH", str(DATA_ROOT / "data" / "attendance.db"))
            ),
            host=os.getenv("DB_HOST", "localhost"),
            port=_env_int("DB_PORT", default_port),
            username=os.getenv("DB_USERNAME", ""),
            password=os.getenv("DB_PASSWORD", ""),
            database_name=os.getenv("DB_NAME", "attendance_system"),
            echo_sql=_env_bool("DB_ECHO_SQL", False),
            pool_size=_env_int("DB_POOL_SIZE", 10),
            max_overflow=_env_int("DB_MAX_OVERFLOW", 20),
            pool_recycle_seconds=_env_int("DB_POOL_RECYCLE_SECONDS", 1800),
            connect_timeout_seconds=_env_int("DB_CONNECT_TIMEOUT_SECONDS", 10),
        )


@dataclass(frozen=True)
class LocaleConfig:
    """Localization defaults, anchored to Iraq as the primary market.

    The application defaults to Arabic with a right-to-left layout;
    English is available as a runtime-switchable secondary language.
    """

    default_language: Language = Language.ARABIC
    supported_languages: tuple[Language, ...] = (Language.ARABIC, Language.ENGLISH)
    locale_name: str = "ar_IQ"
    date_format: str = "%d/%m/%Y"
    time_format: str = "%H:%M"
    use_24_hour_clock: bool = True
    currency_code: str = "IQD"
    currency_symbol: str = "د.ع"
    first_day_of_week: int = 6  # Saturday, ISO weekday-style (0 = Monday)

    @property
    def is_rtl_default(self) -> bool:
        """Whether the default language renders right-to-left."""
        return self.default_language is Language.ARABIC


@dataclass(frozen=True)
class SecurityConfig:
    """Authentication, session and hashing policy."""

    bcrypt_rounds: int = 12
    session_timeout_minutes: int = 30
    max_login_attempts: int = 5
    login_lockout_minutes: int = 15
    minimum_password_length: int = 8
    secret_key: str = field(
        default_factory=lambda: os.getenv(
            "APP_SECRET_KEY", "change-this-secret-key-in-production"
        )
    )

    @classmethod
    def from_env(cls) -> "SecurityConfig":
        """Build a :class:`SecurityConfig` from environment variables."""
        return cls(
            bcrypt_rounds=_env_int("SECURITY_BCRYPT_ROUNDS", 12),
            session_timeout_minutes=_env_int("SECURITY_SESSION_TIMEOUT_MINUTES", 30),
            max_login_attempts=_env_int("SECURITY_MAX_LOGIN_ATTEMPTS", 5),
            login_lockout_minutes=_env_int("SECURITY_LOGIN_LOCKOUT_MINUTES", 15),
            minimum_password_length=_env_int("SECURITY_MIN_PASSWORD_LENGTH", 8),
        )


@dataclass(frozen=True)
class UIConfig:
    """Desktop UI defaults: theme, window sizing and behavior."""

    default_theme: Theme = Theme.LIGHT
    window_min_width: int = 1280
    window_min_height: int = 800
    sidebar_width_expanded: int = 260
    sidebar_width_collapsed: int = 72
    splash_screen_duration_ms: int = 1800
    animation_duration_ms: int = 220
    default_font_family_ar: str = "Cairo"
    default_font_family_en: str = "Segoe UI"
    default_font_size: int = 10

    @classmethod
    def from_env(cls) -> "UIConfig":
        """Build a :class:`UIConfig` from environment variables."""
        theme_raw = os.getenv("UI_DEFAULT_THEME", Theme.LIGHT.value)
        try:
            theme = Theme(theme_raw.lower())
        except ValueError:
            theme = Theme.LIGHT
        return cls(default_theme=theme)


@dataclass(frozen=True)
class BackupConfig:
    """Automatic backup scheduling policy."""

    auto_backup_enabled: bool = True
    interval_hours: int = 24
    retention_count: int = 14
    backup_on_startup: bool = False

    @classmethod
    def from_env(cls) -> "BackupConfig":
        """Build a :class:`BackupConfig` from environment variables."""
        return cls(
            auto_backup_enabled=_env_bool("BACKUP_AUTO_ENABLED", True),
            interval_hours=_env_int("BACKUP_INTERVAL_HOURS", 24),
            retention_count=_env_int("BACKUP_RETENTION_COUNT", 14),
            backup_on_startup=_env_bool("BACKUP_ON_STARTUP", False),
        )


@dataclass(frozen=True)
class DeviceConfig:
    """Default connection parameters for biometric attendance devices."""

    zkteco_default_port: int = 4370
    zkteco_default_timeout_seconds: int = 8
    hikvision_default_port: int = 80
    hikvision_default_timeout_seconds: int = 8
    connection_retry_attempts: int = 3
    discovery_timeout_seconds: int = 5
    auto_sync_enabled: bool = True
    auto_sync_interval_minutes: int = 15

    @classmethod
    def from_env(cls) -> "DeviceConfig":
        """Build a :class:`DeviceConfig` from environment variables."""
        return cls(
            zkteco_default_port=_env_int("DEVICE_ZKTECO_PORT", 4370),
            zkteco_default_timeout_seconds=_env_int("DEVICE_ZKTECO_TIMEOUT", 8),
            hikvision_default_port=_env_int("DEVICE_HIKVISION_PORT", 80),
            hikvision_default_timeout_seconds=_env_int("DEVICE_HIKVISION_TIMEOUT", 8),
            connection_retry_attempts=_env_int("DEVICE_RETRY_ATTEMPTS", 3),
            discovery_timeout_seconds=_env_int("DEVICE_DISCOVERY_TIMEOUT", 5),
            auto_sync_enabled=_env_bool("DEVICE_AUTO_SYNC_ENABLED", True),
            auto_sync_interval_minutes=_env_int("DEVICE_AUTO_SYNC_INTERVAL_MINUTES", 15),
        )


@dataclass(frozen=True)
class ApiConfig:
    """Optional REST API layer settings (see ``api/app.py``).

    The desktop application itself never starts this server — it is a
    separate, optional process (``run_api.py``) for integrations
    (mobile apps, external HR/payroll systems) that need programmatic
    access to the same company data the desktop UI manages. Disabled
    by default so a plain desktop install never opens a network port.
    """

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8000
    token_expires_minutes: int = 480

    @classmethod
    def from_env(cls) -> "ApiConfig":
        """Build an :class:`ApiConfig` from environment variables."""
        return cls(
            enabled=_env_bool("API_ENABLED", False),
            host=os.getenv("API_HOST", "127.0.0.1"),
            port=_env_int("API_PORT", 8000),
            token_expires_minutes=_env_int("API_TOKEN_EXPIRES_MINUTES", 480),
        )


@dataclass(frozen=True)
class LoggingConfig:
    """Application logging policy (see ``utils/logger.py`` for the sink)."""

    level: str = "INFO"
    rotation: str = "10 MB"
    retention: str = "30 days"
    log_file_name: str = "attendance_system.log"

    @classmethod
    def from_env(cls) -> "LoggingConfig":
        """Build a :class:`LoggingConfig` from environment variables."""
        return cls(
            level=os.getenv("LOG_LEVEL", "INFO").upper(),
            rotation=os.getenv("LOG_ROTATION", "10 MB"),
            retention=os.getenv("LOG_RETENTION", "30 days"),
        )


@dataclass(frozen=True)
class AppConfig:
    """Aggregate root for every configuration section in the application."""

    app_name: str = "Attendance Management System"
    app_name_ar: str = "نظام إدارة الحضور والانصراف"
    app_version: str = "1.0.2"
    organization_name: str = "Attendance Systems"
    environment: Environment = Environment.PRODUCTION

    paths: PathsConfig = field(default_factory=PathsConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    locale: LocaleConfig = field(default_factory=LocaleConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)
    device: DeviceConfig = field(default_factory=DeviceConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    @classmethod
    def load(cls) -> "AppConfig":
        """Load the full application configuration from the environment.

        This is the single entry point for building a fully-populated
        :class:`AppConfig`. It also guarantees that every writable runtime
        directory declared in :class:`PathsConfig` exists before the rest
        of the application starts using them.

        Returns:
            A ready-to-use :class:`AppConfig` instance.
        """
        env_raw = os.getenv("APP_ENVIRONMENT", Environment.PRODUCTION.value)
        try:
            environment = Environment(env_raw.lower())
        except ValueError:
            environment = Environment.PRODUCTION

        paths = PathsConfig()
        paths.ensure_created()

        return cls(
            app_name=os.getenv("APP_NAME", cls.app_name),
            app_name_ar=os.getenv("APP_NAME_AR", cls.app_name_ar),
            app_version=os.getenv("APP_VERSION", cls.app_version),
            organization_name=os.getenv("APP_ORGANIZATION_NAME", cls.organization_name),
            environment=environment,
            paths=paths,
            database=DatabaseConfig.from_env(),
            locale=LocaleConfig(),
            security=SecurityConfig.from_env(),
            ui=UIConfig.from_env(),
            backup=BackupConfig.from_env(),
            device=DeviceConfig.from_env(),
            api=ApiConfig.from_env(),
            logging=LoggingConfig.from_env(),
        )


_config_instance: AppConfig | None = None


def get_config() -> AppConfig:
    """Return the process-wide :class:`AppConfig` singleton.

    The configuration is loaded lazily on first access and cached for the
    lifetime of the process, so environment variables and the ``.env``
    file are only read once per run.

    Returns:
        The shared :class:`AppConfig` instance.
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = AppConfig.load()
    return _config_instance

"""Application-wide logging setup, built on Loguru.

Call :func:`setup_logging` once, at application startup (the
composition root in ``main.py``), before anything else logs. After
that:

* Import the pre-configured global logger directly::

      from utils.logger import logger
      logger.info("Employee {employee_number} checked in", employee_number="EMP-0001")

* Or bind structured, multi-tenant context (recommended for anything
  happening on behalf of a specific company/user)::

      from utils.logger import get_logger
      log = get_logger(company_id=company.id, user_id=user.id)
      log.warning("Device {device} went offline", device=device.name)

Every record — including ones produced by plain ``import logging;
logging.getLogger(__name__)`` calls already present elsewhere in this
codebase (e.g. ``database/database.py``) — is redirected into the same
Loguru sinks via :class:`InterceptHandler`, so no other module needs to
change in order to benefit from this setup.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from loguru import logger as _logger

from config import Environment, LoggingConfig, PathsConfig, get_config

logger = _logger
"""The process-wide Loguru logger. Safe to import and use directly once
:func:`setup_logging` has run."""

_CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan>"
    "{extra[context_suffix]} - <level>{message}</level>"
)
_FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{name}:{function}:{line}{extra[context_suffix]} - {message}"
)


def _patch_record(record: dict[str, Any]) -> None:
    """Inject a ``context_suffix`` extra field derived from bound context.

    Runs on every record before formatting, so the format strings above
    can always reference ``{extra[context_suffix]}`` — including for
    records that were never bound with any context — without raising a
    ``KeyError``.
    """
    extra = record["extra"]
    parts = [
        f"{key}={extra[key]}"
        for key in ("company_id", "user_id", "device_id")
        if key in extra
    ]
    record["extra"]["context_suffix"] = f" [{' '.join(parts)}]" if parts else ""


class InterceptHandler(logging.Handler):
    """Redirects standard-library ``logging`` records into Loguru.

    This is what lets every part of the codebase that predates this
    module (or any third-party dependency using plain ``logging``, such
    as SQLAlchemy's engine echo) flow through the same rotating,
    multi-tenant-aware sinks configured here.
    """

    def emit(self, record: logging.LogRecord) -> None:
        """Forward one stdlib :class:`logging.LogRecord` into Loguru."""
        try:
            level: int | str = _logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Walk up from this frame until we leave logging's own call chain
        # (Logger.info -> Logger._log -> Logger.handle -> ... -> here),
        # so Loguru attributes the record to the real caller (e.g.
        # database.database) instead of to logging's internal dispatch
        # frames.
        frame, depth = sys._getframe(1), 1
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        _logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def get_logger(**context: Any) -> Any:
    """Return a Loguru logger bound with structured context.

    Args:
        **context: Arbitrary key/value pairs to attach to every record
            emitted through the returned logger. ``company_id``,
            ``user_id`` and ``device_id`` are rendered inline in the log
            output (see :func:`_patch_record`); any other keys are still
            captured in Loguru's structured ``extra`` dict for
            downstream processing (e.g. a future log-shipping sink).

    Returns:
        A Loguru logger instance with the given context bound.
    """
    return _logger.bind(**context)


def setup_logging(
    logging_config: LoggingConfig | None = None,
    paths_config: PathsConfig | None = None,
    *,
    environment: Environment | None = None,
) -> None:
    """Configure every logging sink for the application.

    Idempotent: safe to call more than once (each call replaces the
    previously configured sinks rather than stacking duplicates), which
    matters for test suites that import multiple modules independently.

    Args:
        logging_config: Logging settings to use; defaults to the
            logging section of :func:`config.get_config`.
        paths_config: Filesystem paths to use; defaults to the paths
            section of :func:`config.get_config`.
        environment: Overrides the environment used to decide whether
            verbose exception diagnostics (local variable values in
            tracebacks) are enabled. Left off in anything but
            :attr:`~config.Environment.DEVELOPMENT`, since it can leak
            sensitive in-memory values (e.g. a password hash being
            processed) into log files.
    """
    app_config = get_config()
    resolved_logging = logging_config or app_config.logging
    resolved_paths = paths_config or app_config.paths
    resolved_environment = environment or app_config.environment
    is_development = resolved_environment is Environment.DEVELOPMENT

    _logger.configure(patcher=_patch_record)
    _logger.remove()

    _logger.add(
        sys.stderr,
        level=resolved_logging.level,
        format=_CONSOLE_FORMAT,
        colorize=True,
        backtrace=is_development,
        diagnose=is_development,
    )

    log_file_path: Path = resolved_paths.logs_dir / resolved_logging.log_file_name
    _logger.add(
        log_file_path,
        level=resolved_logging.level,
        format=_FILE_FORMAT,
        rotation=resolved_logging.rotation,
        retention=resolved_logging.retention,
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=is_development,
    )

    error_log_path = log_file_path.with_name(
        f"{log_file_path.stem}.errors{log_file_path.suffix}"
    )
    _logger.add(
        error_log_path,
        level="ERROR",
        format=_FILE_FORMAT,
        rotation=resolved_logging.rotation,
        retention=resolved_logging.retention,
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=is_development,
    )

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for noisy_logger_name in ("sqlalchemy.engine", "urllib3", "PySide6"):
        noisy_logger = logging.getLogger(noisy_logger_name)
        noisy_logger.handlers = [InterceptHandler()]
        noisy_logger.propagate = False

    _logger.info(
        "Logging initialized (level={level}, environment={environment})",
        level=resolved_logging.level,
        environment=resolved_environment.value,
    )

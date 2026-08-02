"""Standalone entrypoint for the optional REST API server.

Run with ``python run_api.py`` (after setting ``API_ENABLED=true`` and,
if needed, ``API_HOST``/``API_PORT`` — see :class:`~config.ApiConfig`).
A separate process from the desktop application (``main.py``); the two
never run each other, but are safe to run side by side against the
same database, exactly like the desktop app and ``run_api.py`` running
concurrently on one machine, or the desktop app on an admin's PC talking
to a database a server elsewhere also exposes through this API.

The database must already be provisioned by running the desktop app at
least once (companies, roles, and the permission catalog are seeded
through its first-run flow, not this one) — this entrypoint only opens
the existing database and creates any missing tables, matching what
:meth:`~database.database.Database.initialize` already does for the
desktop app.
"""

from __future__ import annotations

import sys

import uvicorn

from config import get_config
from database.database import get_database
from utils.logger import logger, setup_logging


def main() -> int:
    """Start the REST API server; returns the process exit code."""
    config = get_config()
    config.paths.ensure_created()
    setup_logging()

    if not config.api.enabled:
        print(
            "The REST API is disabled (API_ENABLED is not set). "
            "Set API_ENABLED=true to run this server.",
            file=sys.stderr,
        )
        return 1

    get_database().initialize()

    logger.info(
        "Starting {app_name} REST API v{version} on {host}:{port}",
        app_name=config.app_name,
        version=config.app_version,
        host=config.api.host,
        port=config.api.port,
    )
    uvicorn.run("api.app:app", host=config.api.host, port=config.api.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

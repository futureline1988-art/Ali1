"""Standalone entrypoint for the Attendance Server.

Run with ``python -m server.main``. A separate process from both the
Attendance Client (``main.py``) and the Developer Suite
(``python -m developer_suite.main``) — none of the three ever import
or run each other; the platform's design rule is that the other two
applications will eventually talk to this one only over HTTP (see
``server/__init__.py``).
"""

from __future__ import annotations

import uvicorn

from server.api.app import create_app
from server.config import ServerConfig, get_server_config
from server.database.bootstrap import build_database
from utils.logger import logger, setup_logging


def main() -> int:
    """Start the Attendance Server; returns the process exit code."""
    config: ServerConfig = get_server_config()
    config.paths.ensure_created()
    setup_logging(
        logging_config=config.logging, paths_config=config.paths, environment=config.environment
    )

    database = build_database(config)

    logger.info(
        "Starting {app_name} v{version} on {host}:{port}",
        app_name=config.app_name,
        version=config.app_version,
        host=config.api.host,
        port=config.api.port,
    )

    app = create_app(config, database)
    uvicorn.run(app, host=config.api.host, port=config.api.port)
    database.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

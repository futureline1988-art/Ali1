"""FastAPI application factory for the Attendance Server.

Mirrors ``api/app.py``'s shape (build the app, mount routers, install a
catch-all error handler that never leaks a raw traceback to the
caller) but takes its configuration and database explicitly rather
than reaching for :func:`config.get_config`/:func:`database.database.get_database` —
this server has no such singletons (see ``server/config.py``'s and
``server/api/dependencies.py``'s docstrings for why), so
:func:`create_app` is the one place that wires a concrete
:class:`~server.config.ServerConfig` and
:class:`~database.database.Database` into the running application,
via ``app.state``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from database.database import Database
from server.api.routers import auth, devices, health, status, sync, version
from server.config import ServerConfig
from server.container import ServiceContainer
from utils.logger import logger


def create_app(config: ServerConfig, database: Database) -> FastAPI:
    """Build and return the configured FastAPI application.

    Args:
        config: This server's configuration.
        database: This server's own, already-initialized database (see
            :func:`server.database.bootstrap.build_database`).

    Returns:
        A ready-to-serve :class:`~fastapi.FastAPI` instance with
        ``/health``, ``/version``, device registration and listing,
        the push/pull/conflict-resolution sync endpoints, the
        administrative status endpoint, the admin authentication
        endpoints (Phase 11 — see ``server/api/routers/auth.py``),
        plus a catch-all error handler. Still no business-domain
        router (customers, licenses, configuration, ...) — see this
        package's parent ``__init__.py``.
    """
    app = FastAPI(
        title=config.app_name,
        description="Attendance Server: single source of truth for the commercial platform.",
        version=config.app_version,
    )
    app.state.config = config
    app.state.container = ServiceContainer(config=config, database=database)
    # Read by server.api.routers.status.get_status to compute uptime -
    # the only piece of "server health" state this application tracks
    # that isn't already available from config or the database itself.
    app.state.started_at = datetime.now(timezone.utc)

    app.include_router(health.router)
    app.include_router(version.router)
    app.include_router(devices.router)
    app.include_router(sync.router)
    app.include_router(status.router)
    app.include_router(auth.router)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Log unexpected errors and return a generic 500 rather than a raw traceback."""
        logger.error(
            "Unhandled Attendance Server error on {method} {path}: {error}",
            method=request.method,
            path=request.url.path,
            error=str(exc),
        )
        return JSONResponse(status_code=500, content={"detail": "Internal server error."})

    return app

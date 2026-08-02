"""FastAPI application factory for the optional REST API layer.

Not started by the desktop app itself — see ``run_api.py`` for the
standalone entrypoint and :class:`~config.ApiConfig` for how it is
enabled/configured. Every router only ever touches the database
through :func:`~api.dependencies.get_db_session`, so this app is safe
to run as a completely separate OS process from the desktop UI,
against the same database file/server.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.routers import attendance, auth, companies, dashboard, departments, employees
from config import get_config
from utils.logger import logger


def create_app() -> FastAPI:
    """Build and return the configured FastAPI application.

    Returns:
        A ready-to-serve :class:`~fastapi.FastAPI` instance with every
        router mounted and a catch-all error handler installed (the
        same "never leak a raw traceback to the caller" rule
        ``controllers/base_controller.py`` enforces for the desktop
        UI).
    """
    config = get_config()
    app = FastAPI(
        title=config.app_name,
        description=(
            "REST API for the "
            f"{config.app_name_ar} / {config.app_name} attendance management system."
        ),
        version=config.app_version,
    )

    app.include_router(auth.router)
    app.include_router(companies.router)
    app.include_router(employees.router)
    app.include_router(departments.router)
    app.include_router(attendance.router)
    app.include_router(dashboard.router)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Log unexpected errors and return a generic 500 rather than a raw traceback."""
        logger.error(
            "Unhandled API error on {method} {path}: {error}",
            method=request.method,
            path=request.url.path,
            error=str(exc),
        )
        return JSONResponse(status_code=500, content={"detail": "Internal server error."})

    @app.get("/api/health")
    def health_check() -> dict[str, str]:
        """Liveness probe, unauthenticated."""
        return {"status": "ok", "version": config.app_version}

    return app


app = create_app()

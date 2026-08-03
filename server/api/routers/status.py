"""Administrative server status: version, database reachability, uptime.

Deliberately separate from the existing, unauthenticated ``/health``
and ``/version`` (see :mod:`server.api.routers.health`/:mod:`server.api.routers.version`,
both left completely unchanged) — this endpoint exposes operational
detail (live database connectivity, process uptime) one level beyond a
bare liveness probe, so it is gated behind ``require_scope("sync:admin")``
like every other administrative endpoint, per Phase 10's explicit
"keep all new server endpoints protected" requirement.

Reuses :meth:`~database.database.Database.check_connection` unmodified
for the database check — no new connectivity-probing logic.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from server.auth.dependencies import AuthenticatedPrincipal, require_scope

router = APIRouter(prefix="/api/v1", tags=["status"])


@router.get("/status")
def get_status(
    request: Request,
    _principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin")),
) -> dict:
    """Report this server's version, live database connectivity, and uptime.

    Returns:
        ``{"app_name", "app_version", "database_connected", "uptime_seconds"}``.
    """
    config = request.app.state.config
    database = request.app.state.container.database
    started_at: datetime = request.app.state.started_at

    uptime_seconds = (datetime.now(timezone.utc) - started_at).total_seconds()
    return {
        "app_name": config.app_name,
        "app_version": config.app_version,
        "database_connected": database.check_connection(),
        "uptime_seconds": uptime_seconds,
    }

"""Server version metadata."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["version"])


@router.get("/version")
def version_info(request: Request) -> dict[str, str]:
    """Unauthenticated server version/build metadata.

    Returns:
        This server's own ``app_name``/``app_version`` — independent
        of the Attendance Client's and Developer Suite's own versions,
        which are released separately (see ``server/config.py``).
    """
    config = request.app.state.config
    return {"app_name": config.app_name, "app_version": config.app_version}

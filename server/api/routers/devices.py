"""Device registration.

The one administrative endpoint in this phase — registering a new
device is gated behind ``require_scope("sync:admin")`` (see
:mod:`server.auth.dependencies`), the interface Phase 6 built
specifically for "whatever future phase adds the first actual
login/registration endpoint." No login flow issuing such a token
exists yet; a test (or, eventually, an authenticated Developer Suite
session) obtains one via :func:`server.auth.tokens.issue_token`
directly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from server.api.schemas import DeviceRegisterRequest
from server.auth.dependencies import AuthenticatedPrincipal, require_scope
from server.services.device_service import DeviceService

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


@router.post("/register", status_code=201)
def register_device(
    body: DeviceRegisterRequest,
    request: Request,
    _principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin")),
) -> dict:
    """Register a new device and return its one-time sync credential.

    Returns:
        ``{"device": {...}, "api_key": "..."}`` — ``api_key`` is shown
        exactly once; only its bcrypt hash is stored (see
        :meth:`~server.services.device_service.DeviceService.register_device`).
    """
    device_service: DeviceService = request.app.state.container.device_service
    device, api_key = device_service.register_device(name=body.name, device_type=body.device_type)
    return {"device": device.to_dict(exclude={"api_key_hash"}), "api_key": api_key}

"""Device registration and read-only device listing.

Registering a new device is gated behind ``require_scope("sync:admin")``
(see :mod:`server.auth.dependencies`), the interface Phase 6 built
specifically for "whatever future phase adds the first actual
login/registration endpoint." No login flow issuing such a token
exists yet; a test (or an authenticated Developer Suite session,
carrying a Phase 10 bootstrap token — see
:mod:`developer_suite.admin.token_provider`) obtains one via
:func:`server.auth.tokens.issue_token` directly.

Phase 10 adds the read-only listing endpoint below, for a Developer
Suite administration dashboard to show registered installations and
their online/offline state — reusing
:meth:`~server.services.device_service.DeviceService.list_devices`
unmodified; no new business logic.
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


@router.get("")
def list_devices(
    request: Request,
    _principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin")),
) -> dict:
    """List every registered device (both Attendance Client and Developer Suite installations).

    Never includes ``api_key_hash`` — same exclusion
    :func:`register_device` already applies.
    """
    device_service: DeviceService = request.app.state.container.device_service
    devices = device_service.list_devices()
    return {"devices": [device.to_dict(exclude={"api_key_hash"}) for device in devices]}

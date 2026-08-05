"""Device registration and read-only device listing.

Registering a new device via ``POST /register`` is gated behind
``require_scope("sync:admin")`` (see :mod:`server.auth.dependencies`)
— the admin-driven path, used by the Developer Suite and by tests.
``POST /self-register`` is the fully-automatic counterpart: no bearer
token at all, used by a fresh Attendance Client installation at first
startup (see :meth:`~sync.coordinator.ClientSyncCoordinator.self_enroll`)
to register itself and get linked to its company's subscription with
no administrator action anywhere.

Phase 10 adds the read-only listing endpoint below, for a Developer
Suite administration dashboard to show registered installations and
their online/offline state — reusing
:meth:`~server.services.device_service.DeviceService.list_devices`
unmodified; no new business logic.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from server.api.schemas import DeviceRegisterRequest, SelfRegisterDeviceRequest
from server.auth.dependencies import AuthenticatedPrincipal, require_scope
from server.services.device_service import (
    DeviceService,
    MaxDevicesReachedError,
    SubscriptionNotActiveError,
    SubscriptionRequiredError,
)
from server.services.subscription_service import SubscriptionService

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


def _device_dict(device, subscription_service: SubscriptionService) -> dict:
    """Serialize a device, including its subscription's company name (if linked).

    ``company_name`` is not a mapped column of
    :class:`~server.models.device.SyncDevice` — resolved here via
    :attr:`~server.models.device.SyncDevice.subscription_id` (a plain
    column, safe to read on a detached instance) rather than the
    ``subscription`` relationship, which would need a still-open
    session to lazy-load and the device service's own session has
    already closed by the time a router handler runs.
    """
    data = device.to_dict(exclude={"api_key_hash"})
    subscription = (
        subscription_service.get(device.subscription_id) if device.subscription_id is not None else None
    )
    data["company_name"] = subscription.company_name if subscription is not None else None
    return data


@router.post("/register", status_code=201)
def register_device(
    body: DeviceRegisterRequest,
    request: Request,
    _principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin")),
) -> dict:
    """Register a new device and return its one-time sync credential.

    For ``device_type=attendance_client``, ``company_name`` must name
    an existing subscription (see
    :mod:`server.api.routers.subscriptions`) with capacity left under
    its device cap.

    Returns:
        ``{"device": {...}, "api_key": "..."}`` — ``api_key`` is shown
        exactly once; only its bcrypt hash is stored (see
        :meth:`~server.services.device_service.DeviceService.register_device`).

    Raises:
        HTTPException: 422 if no subscription exists for
            ``company_name``, 403 if that subscription has already
            reached :attr:`~server.models.subscription.Subscription.max_devices`.
    """
    device_service: DeviceService = request.app.state.container.device_service
    subscription_service: SubscriptionService = request.app.state.container.subscription_service
    try:
        device, api_key = device_service.register_device(
            name=body.name, device_type=body.device_type, company_name=body.company_name
        )
    except SubscriptionRequiredError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except MaxDevicesReachedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return {"device": _device_dict(device, subscription_service), "api_key": api_key}


#: The single, identical message for every "this company code is not
#: usable right now" case (no such code, or a real code whose
#: subscription is suspended/expired). Deliberately generic and
#: deliberately the *same* string/status for both cases — this
#: endpoint is unauthenticated and this server is multi-tenant, so a
#: caller must never be able to tell "not a real code" apart from
#: "real code, just inactive" by comparing error messages, which would
#: let company codes be enumerated one guess at a time.
_INVALID_COMPANY_CODE_DETAIL = "Invalid or inactive company code."


@router.post("/self-register", status_code=201)
def self_register_device(body: SelfRegisterDeviceRequest, request: Request) -> dict:
    """Fully-automatic device registration — no bearer token required.

    The onboarding path a fresh Attendance Client installation drives
    at first startup, entirely on its own: given only a ``company_code``
    (see :class:`~server.models.subscription.Subscription`), this
    either links the new device to that company's active subscription
    immediately (if it has capacity) or rejects the request with a
    generic reason. No administrator action, and no manual
    subscription-to-device linking in the Developer Suite, is ever
    involved.

    Returns:
        Same shape as :func:`register_device`.

    Raises:
        HTTPException: 422 (identical message/status for both) if no
            subscription exists for ``company_code`` or it exists but
            is suspended/expired — see :data:`_INVALID_COMPANY_CODE_DETAIL`;
            403 ("Maximum allowed devices reached.") if that
            subscription's device cap is already reached.
    """
    device_service: DeviceService = request.app.state.container.device_service
    subscription_service: SubscriptionService = request.app.state.container.subscription_service
    try:
        device, api_key = device_service.self_register_device(
            name=body.name, company_code=body.company_code
        )
    except (SubscriptionRequiredError, SubscriptionNotActiveError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=_INVALID_COMPANY_CODE_DETAIL
        ) from exc
    except MaxDevicesReachedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Maximum allowed devices reached."
        ) from exc
    return {"device": _device_dict(device, subscription_service), "api_key": api_key}


@router.get("")
def list_devices(
    request: Request,
    _principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin", "sync:read")),
) -> dict:
    """List every registered device (both Attendance Client and Developer Suite installations).

    Never includes ``api_key_hash`` — same exclusion
    :func:`register_device` already applies. Read-only, so a
    ``sync:read``-scoped token (e.g. a Phase 11 ``VIEWER`` admin
    account) suffices, not only ``sync:admin``. Each entry includes
    ``company_name`` (``None`` for a device with no linked
    subscription) so the Developer Suite's device listings need no
    second lookup to show which company a device belongs to.
    """
    device_service: DeviceService = request.app.state.container.device_service
    subscription_service: SubscriptionService = request.app.state.container.subscription_service
    devices = device_service.list_devices()
    return {"devices": [_device_dict(device, subscription_service) for device in devices]}

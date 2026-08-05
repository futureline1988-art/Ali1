"""Company subscriptions: admin management + the device-facing status check.

Two audiences, exactly like :mod:`server.api.routers.updates` already
splits admin management from device-facing endpoints:

* The Developer Suite creates, renews, suspends, and reactivates
  subscriptions -- gated behind ``require_scope("sync:admin")``, same
  as every other administrative endpoint on this server.
* The Attendance Client checks its own installation's subscription
  status at login/startup -- authenticated the same way sync push/pull
  and update checks already are (see :mod:`server.auth.device_auth`),
  reusing the device's existing long-lived credential rather than a
  second mechanism.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from server.api.schemas import (
    CreateSubscriptionRequest,
    SetInitialAdminRequest,
    UpdateSubscriptionRequest,
    UpdateSupportInfoRequest,
)
from server.auth.dependencies import AuthenticatedPrincipal, require_scope
from server.auth.device_auth import get_authenticated_device
from server.models.device import SyncDevice
from server.models.subscription import SubscriptionStatus
from server.services.initial_admin_service import (
    InitialAdminPasswordPolicyError,
    InitialAdminService,
    SubscriptionNotFoundForInitialAdminError,
)
from server.services.subscription_service import (
    DuplicateCompanyNameError,
    SubscriptionNotFoundError,
    SubscriptionService,
)

router = APIRouter(tags=["subscriptions"])

_SUPPORT_INFO_FIELDS = (
    "support_phone_primary",
    "support_phone_secondary",
    "support_whatsapp",
    "support_email",
    "support_hours",
    "support_message",
)


def _support_info_dict(subscription) -> dict:
    """The support-info subset of a subscription, shared by the admin and device-facing responses."""
    return {field: getattr(subscription, field) for field in _SUPPORT_INFO_FIELDS}


def _subscription_dict(subscription) -> dict:
    """Serialize a subscription, including its computed status/days-remaining."""
    data = subscription.to_dict()
    data["is_expired"] = subscription.is_expired
    data["is_active"] = subscription.is_active
    data["days_remaining"] = subscription.days_remaining
    return data


# ---------------------------------------------------------------------------
# Admin: create / list / get / update.
# ---------------------------------------------------------------------------


@router.post("/api/v1/subscriptions", status_code=201)
def create_subscription(
    body: CreateSubscriptionRequest,
    request: Request,
    _principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin")),
) -> dict:
    """Create a new company subscription."""
    subscription_service: SubscriptionService = request.app.state.container.subscription_service
    try:
        subscription = subscription_service.create(
            company_name=body.company_name,
            subscription_start_date=body.subscription_start_date,
            subscription_end_date=body.subscription_end_date,
            max_devices=body.max_devices,
            max_users=body.max_users,
        )
    except DuplicateCompanyNameError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _subscription_dict(subscription)


@router.get("/api/v1/subscriptions")
def list_subscriptions(
    request: Request,
    _principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin", "sync:read")),
) -> dict:
    """List every subscription."""
    subscription_service: SubscriptionService = request.app.state.container.subscription_service
    subscriptions = subscription_service.list_all()
    result = []
    for subscription in subscriptions:
        entry = _subscription_dict(subscription)
        entry["device_count"] = subscription_service.device_count(subscription.id)
        result.append(entry)
    return {"subscriptions": result}


@router.get("/api/v1/subscriptions/{subscription_id}")
def get_subscription(
    subscription_id: int,
    request: Request,
    _principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin", "sync:read")),
) -> dict:
    """Fetch a single subscription, including its current device count."""
    subscription_service: SubscriptionService = request.app.state.container.subscription_service
    subscription = subscription_service.get(subscription_id)
    if subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such subscription.")
    data = _subscription_dict(subscription)
    data["device_count"] = subscription_service.device_count(subscription_id)
    return data


@router.patch("/api/v1/subscriptions/{subscription_id}")
def update_subscription(
    subscription_id: int,
    body: UpdateSubscriptionRequest,
    request: Request,
    _principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin")),
) -> dict:
    """Renew, suspend/reactivate, and/or change the device/user caps of a subscription."""
    subscription_service: SubscriptionService = request.app.state.container.subscription_service
    try:
        if body.action == "suspend":
            subscription_service.suspend(subscription_id)
        elif body.action == "reactivate":
            subscription_service.reactivate(subscription_id)

        if body.subscription_end_date is not None:
            subscription_service.renew(subscription_id, new_end_date=body.subscription_end_date)

        if body.max_devices is not None or body.max_users_unlimited:
            subscription_service.update_limits(
                subscription_id,
                max_devices=body.max_devices,
                max_users=None if body.max_users_unlimited else body.max_users,
            )
        elif body.max_users is not None:
            subscription_service.update_limits(subscription_id, max_users=body.max_users)

        subscription = subscription_service.get(subscription_id)
    except SubscriptionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    data = _subscription_dict(subscription)
    data["device_count"] = subscription_service.device_count(subscription_id)
    return data


@router.patch("/api/v1/subscriptions/{subscription_id}/support-info")
def update_support_info(
    subscription_id: int,
    body: UpdateSupportInfoRequest,
    request: Request,
    _principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin")),
) -> dict:
    """Set this company's Support Information, shown to its Attendance Clients.

    Developer-Suite-only (see :class:`~server.api.schemas.UpdateSupportInfoRequest`'s
    own docstring); the Attendance Client only ever reads these fields
    back via ``GET /api/v1/subscription/status``.
    """
    subscription_service: SubscriptionService = request.app.state.container.subscription_service
    subscription = subscription_service.get(subscription_id)
    if subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such subscription.")
    updates = body.model_dump(exclude_unset=True)
    subscription = subscription_service.update_support_info(subscription_id, **updates)
    return _support_info_dict(subscription)


# ---------------------------------------------------------------------------
# Admin: the subscription's initial Company Administrator.
# ---------------------------------------------------------------------------


@router.put("/api/v1/subscriptions/{subscription_id}/initial-admin")
def set_initial_admin(
    subscription_id: int,
    body: SetInitialAdminRequest,
    request: Request,
    _principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin")),
) -> dict:
    """Create or replace this subscription's initial Company Administrator.

    Developer-Suite-only -- see :mod:`server.services.initial_admin_service`'s
    own docstring for why the Attendance Client must never call this.
    """
    initial_admin_service: InitialAdminService = request.app.state.container.initial_admin_service
    try:
        account = initial_admin_service.set_initial_admin(
            subscription_id,
            username=body.username,
            full_name=body.full_name,
            password=body.password,
        )
    except SubscriptionNotFoundForInitialAdminError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InitialAdminPasswordPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"username": account.username, "full_name": account.full_name}


@router.get("/api/v1/subscriptions/{subscription_id}/initial-admin")
def get_initial_admin_admin(
    subscription_id: int,
    request: Request,
    _principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin", "sync:read")),
) -> dict:
    """Fetch this subscription's currently-configured initial administrator, if any.

    Never returns the password hash -- this is for the Developer Suite
    to display *whether* one is configured and its username/full name,
    not to expose the credential itself.
    """
    initial_admin_service: InitialAdminService = request.app.state.container.initial_admin_service
    account = initial_admin_service.get_for_subscription(subscription_id)
    if account is None:
        return {"configured": False}
    return {"configured": True, "username": account.username, "full_name": account.full_name}


# ---------------------------------------------------------------------------
# Device-facing: this installation's own subscription status.
# ---------------------------------------------------------------------------


@router.get("/api/v1/subscription/status")
def get_subscription_status(
    request: Request, device: SyncDevice = Depends(get_authenticated_device)
) -> dict:
    """Report the calling device's own subscription status.

    Returns:
        ``{"status": "active"|"suspended"|"expired"|"not_linked", ...}``
        — ``"not_linked"`` covers the (should-never-happen-in-practice)
        case of a device with no :attr:`~server.models.device.SyncDevice.subscription_id`,
        e.g. a Developer Suite-type device mistakenly calling this
        endpoint, or a stale row from before this feature existed.
    """
    subscription_service: SubscriptionService = request.app.state.container.subscription_service
    if device.subscription_id is None:
        return {"status": "not_linked"}

    subscription = subscription_service.get(device.subscription_id)
    if subscription is None:
        return {"status": "not_linked"}

    if subscription.is_expired:
        effective_status = "expired"
    elif subscription.status is SubscriptionStatus.SUSPENDED:
        effective_status = "suspended"
    else:
        effective_status = "active"

    data = {
        "status": effective_status,
        "company_name": subscription.company_name,
        "subscription_start_date": subscription.subscription_start_date.isoformat(),
        "subscription_end_date": subscription.subscription_end_date.isoformat(),
        "max_devices": subscription.max_devices,
        "max_users": subscription.max_users,
        "device_count": subscription_service.device_count(subscription.id),
        "days_remaining": subscription.days_remaining,
    }
    data.update(_support_info_dict(subscription))
    return data


@router.get("/api/v1/subscription/initial-admin")
def get_initial_admin(
    request: Request, device: SyncDevice = Depends(get_authenticated_device)
) -> dict:
    """Download this device's own company's initial administrator credential.

    Idempotent, not consumed on fetch (see
    :mod:`server.models.initial_admin`'s own docstring for why): every
    independent Attendance Client installation linked to this
    subscription bootstraps its own local ``User`` row from the same
    downloaded hash, and never sends the plaintext password anywhere.

    Returns:
        ``{"configured": False}`` if the Developer Suite has not yet
        set an initial administrator for this device's subscription;
        otherwise ``{"configured": True, "username", "full_name",
        "password_hash"}``.
    """
    initial_admin_service: InitialAdminService = request.app.state.container.initial_admin_service
    if device.subscription_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device is not linked to a subscription.")
    account = initial_admin_service.get_for_subscription(device.subscription_id)
    if account is None:
        return {"configured": False}
    return {
        "configured": True,
        "username": account.username,
        "full_name": account.full_name,
        "password_hash": account.password_hash,
    }

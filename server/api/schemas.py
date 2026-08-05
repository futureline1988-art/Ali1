"""Pydantic request models for the Attendance Server API.

Mirrors ``api/schemas.py``'s own convention exactly: Pydantic models
here validate *requests*; response bodies are plain ``dict`` built from
:meth:`~models.base.SerializationMixin.to_dict` (now part of
:class:`~server.database.base.ServerBaseModel`'s composition — see
that module's docstring), the same ORM-to-dict path
``api/schemas.py``'s own docstring describes.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from server.models.device import DeviceType
from server.models.sync import SyncOperation
from server.models.update import DeviceUpdateStatusValue, TargetScope, UpdateType


class DeviceRegisterRequest(BaseModel):
    """POST /api/v1/devices/register request body."""

    name: str = Field(min_length=1, max_length=200)
    device_type: DeviceType
    company_name: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Required when device_type is attendance_client -- the exact "
            "Subscription.company_name this installation belongs to. Ignored for "
            "developer_suite devices."
        ),
    )


class SelfRegisterDeviceRequest(BaseModel):
    """POST /api/v1/devices/self-register request body.

    No bearer token accompanies this request -- see
    :func:`server.api.routers.devices.self_register_device`. Always an
    ``attendance_client`` device; there is no self-service registration
    for ``developer_suite`` installations. Identifies the company by
    its opaque, system-generated ``company_code`` (see
    :class:`~server.models.subscription.Subscription`) rather than its
    name -- this server is multi-tenant and this endpoint is
    unauthenticated, so a company name (guessable, and the whole point
    of the company) must never be usable to look anything up here.
    """

    name: str = Field(min_length=1, max_length=200)
    company_code: str = Field(min_length=1, max_length=64)


class CreateSubscriptionRequest(BaseModel):
    """POST /api/v1/subscriptions request body."""

    company_name: str = Field(min_length=1, max_length=200)
    subscription_start_date: date
    subscription_end_date: date
    max_devices: int = Field(ge=1)
    max_users: int | None = Field(default=None, ge=1)


class UpdateSubscriptionRequest(BaseModel):
    """PATCH /api/v1/subscriptions/{id} request body.

    Every field is optional -- only the fields present are changed.
    ``action`` (when given) applies before the other fields: it exists
    so a single call can e.g. reactivate a subscription and renew its
    end date at once.
    """

    action: str | None = Field(default=None, pattern="^(suspend|reactivate)$")
    subscription_end_date: date | None = None
    max_devices: int | None = Field(default=None, ge=1)
    max_users: int | None = Field(default=None, ge=1)
    max_users_unlimited: bool = Field(
        default=False,
        description="Set true to explicitly clear max_users back to unlimited.",
    )


class UpdateSupportInfoRequest(BaseModel):
    """PATCH /api/v1/subscriptions/{id}/support-info request body.

    Every field is optional and independently nullable -- omitted
    fields are left unchanged, fields explicitly set to ``null`` are
    cleared. Developer-Suite-only; the Attendance Client only ever
    reads these values back via ``GET /api/v1/subscription/status``,
    see :mod:`server.models.subscription`'s own docstring.
    """

    support_phone_primary: str | None = Field(default=None, max_length=50)
    support_phone_secondary: str | None = Field(default=None, max_length=50)
    support_whatsapp: str | None = Field(default=None, max_length=50)
    support_email: str | None = Field(default=None, max_length=255)
    support_hours: str | None = Field(default=None, max_length=200)
    support_message: str | None = Field(default=None, max_length=1000)


class SetInitialAdminRequest(BaseModel):
    """PUT /api/v1/subscriptions/{id}/initial-admin request body.

    Developer-Suite-only -- see
    :mod:`server.services.initial_admin_service`'s own docstring for
    why the Attendance Client must never be able to call this. The
    password is plaintext over the wire exactly once, from the
    Developer Suite operator's own machine to this server, which
    hashes it immediately (see
    :meth:`~server.services.initial_admin_service.InitialAdminService.set_initial_admin`)
    -- the Attendance Client later downloads only the resulting hash,
    never this plaintext.
    """

    username: str = Field(min_length=1, max_length=64)
    full_name: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1)


class ChangeItemRequest(BaseModel):
    """One entry in a POST /api/v1/sync/push request body's ``changes`` list."""

    entity_type: str = Field(min_length=1, max_length=100)
    entity_id: str = Field(min_length=1, max_length=64)
    operation: SyncOperation
    payload: dict
    checksum: str = Field(min_length=64, max_length=64)
    base_version: int = Field(default=0, ge=0)


class PushRequest(BaseModel):
    """POST /api/v1/sync/push request body."""

    changes: list[ChangeItemRequest] = Field(min_length=1, max_length=500)


class ResolveConflictRequest(BaseModel):
    """POST /api/v1/sync/conflicts/{change_id}/resolve request body."""

    apply_incoming: bool


class AdminSetupRequest(BaseModel):
    """POST /api/v1/auth/setup request body."""

    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1)
    full_name: str | None = Field(default=None, max_length=200)


class AdminLoginRequest(BaseModel):
    """POST /api/v1/auth/login request body."""

    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1)
    user_agent: str | None = Field(default=None, max_length=200)


class AdminRefreshRequest(BaseModel):
    """POST /api/v1/auth/refresh request body."""

    refresh_token: str = Field(min_length=1)


class AdminLogoutRequest(BaseModel):
    """POST /api/v1/auth/logout request body."""

    refresh_token: str = Field(min_length=1)


class AdminChangePasswordRequest(BaseModel):
    """POST /api/v1/auth/change-password request body."""

    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=1)


class AdminPasswordResetRequestBody(BaseModel):
    """POST /api/v1/auth/password-reset/request request body."""

    username: str = Field(min_length=1, max_length=100)


class AdminPasswordResetCompleteRequest(BaseModel):
    """POST /api/v1/auth/password-reset/complete request body."""

    reset_token: str = Field(min_length=1)
    new_password: str = Field(min_length=1)


class CreateUpdateVersionRequest(BaseModel):
    """POST /api/v1/updates/versions request body."""

    version: str = Field(min_length=1, max_length=50)
    release_notes: str | None = None
    min_supported_version: str | None = Field(default=None, max_length=50)
    update_type: UpdateType = UpdateType.OPTIONAL


class SetUpdateTargetsRequest(BaseModel):
    """PUT /api/v1/updates/versions/{id}/targets request body."""

    scope: TargetScope
    device_public_ids: list[str] = Field(default_factory=list)


class ScheduleUpdateRequest(BaseModel):
    """POST /api/v1/updates/versions/{id}/schedule request body."""

    scheduled_at: datetime


class RollbackUpdateRequest(BaseModel):
    """POST /api/v1/updates/versions/{id}/rollback request body."""

    reason: str | None = None


class ReportUpdateStatusRequest(BaseModel):
    """POST /api/v1/updates/status request body."""

    update_version_id: int
    status: DeviceUpdateStatusValue
    progress_percent: int = Field(default=0, ge=0, le=100)
    error_message: str | None = None

"""Pydantic request models for the Attendance Server API.

Mirrors ``api/schemas.py``'s own convention exactly: Pydantic models
here validate *requests*; response bodies are plain ``dict`` built from
:meth:`~models.base.SerializationMixin.to_dict` (now part of
:class:`~server.database.base.ServerBaseModel`'s composition — see
that module's docstring), the same ORM-to-dict path
``api/schemas.py``'s own docstring describes.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from server.models.device import DeviceType
from server.models.sync import SyncOperation
from server.models.update import DeviceUpdateStatusValue, TargetScope, UpdateType


class DeviceRegisterRequest(BaseModel):
    """POST /api/v1/devices/register request body."""

    name: str = Field(min_length=1, max_length=200)
    device_type: DeviceType


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

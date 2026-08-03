"""Pydantic request models for the Attendance Server API.

Mirrors ``api/schemas.py``'s own convention exactly: Pydantic models
here validate *requests*; response bodies are plain ``dict`` built from
:meth:`~models.base.SerializationMixin.to_dict` (now part of
:class:`~server.database.base.ServerBaseModel`'s composition — see
that module's docstring), the same ORM-to-dict path
``api/schemas.py``'s own docstring describes.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from server.models.device import DeviceType
from server.models.sync import SyncOperation


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

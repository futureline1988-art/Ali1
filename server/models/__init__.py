"""Attendance Server ORM models.

Phase 7 adds the first concrete models: device registration
(:mod:`server.models.device`) and the generic change-tracking ledger
that powers synchronization (:mod:`server.models.sync`). Neither
represents a specific business domain (customers, licenses,
configuration, ...) — those stay out of scope until their own approved
phases wire them into this same generic sync mechanism (see
:mod:`server.services.sync_service`'s docstring).

Phase 11 adds the second, entirely independent concept this server
tracks: real person admin accounts and their login sessions
(:mod:`server.models.admin_account`, :mod:`server.models.admin_session`,
:mod:`server.models.admin_password_reset`, :mod:`server.models.admin_audit_log`)
— unrelated to :class:`~server.models.device.SyncDevice`, which
represents an *installation*'s non-interactive sync credential, not a
person.
"""

from __future__ import annotations

from server.models.admin_account import AdminAccount, AdminRole
from server.models.admin_audit_log import AdminAuditAction, AdminAuditLog
from server.models.admin_password_reset import AdminPasswordResetToken
from server.models.admin_session import AdminSession
from server.models.device import SyncDevice, DeviceType
from server.models.sync import ChangeRecord, ChangeStatus, EntityVersion, SyncOperation, SyncSequence

__all__ = [
    "SyncDevice",
    "DeviceType",
    "ChangeRecord",
    "ChangeStatus",
    "EntityVersion",
    "SyncOperation",
    "SyncSequence",
    "AdminAccount",
    "AdminRole",
    "AdminSession",
    "AdminPasswordResetToken",
    "AdminAuditLog",
    "AdminAuditAction",
]

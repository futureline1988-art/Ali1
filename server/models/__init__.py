"""Attendance Server ORM models.

Phase 7 adds the first concrete models: device registration
(:mod:`server.models.device`) and the generic change-tracking ledger
that powers synchronization (:mod:`server.models.sync`). Neither
represents a specific business domain (customers, licenses,
configuration, ...) — those stay out of scope until their own approved
phases wire them into this same generic sync mechanism (see
:mod:`server.services.sync_service`'s docstring).
"""

from __future__ import annotations

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
]

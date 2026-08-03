"""The wire-level contract a Developer Suite sync client must honor.

This module intentionally does not import anything from ``server`` —
the Attendance Server and the Developer Suite communicate over HTTP
only (see the Phase 6 architecture note: "The Attendance Server
remains the central backend that both the Attendance Client and the
Developer Suite communicate with over HTTP"). A Python-level import of
``server.services.sync_service`` from here would quietly reintroduce a
process-level coupling the HTTP boundary exists specifically to avoid
— so instead of importing, this module *replicates* the two pieces of
the protocol a client must reproduce byte-for-byte to interoperate:
the checksum algorithm and the set of valid operations. Both are
already documented as a "protocol contract any future real client must
replicate exactly" in :mod:`server.models.sync`; this is that
replication.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum

from developer_suite.models.sync_state import SyncOperation

__all__ = ["SyncOperation", "DeviceType", "ChangeStatus", "compute_checksum"]


class DeviceType(str, Enum):
    """Mirrors :class:`server.models.device.DeviceType`'s values exactly.

    Sent as the ``device_type`` field of a
    ``POST /api/v1/devices/register`` request body — see
    :func:`~developer_suite.sync.client.register_device`.
    """

    DEVELOPER_SUITE = "developer_suite"
    ATTENDANCE_CLIENT = "attendance_client"


class ChangeStatus(str, Enum):
    """Mirrors :class:`server.models.sync.ChangeStatus`'s values exactly.

    Read back from a ``POST /api/v1/sync/push`` response's per-change
    ``status`` field — see
    :class:`~developer_suite.sync.client.PushResultItem`.
    """

    APPLIED = "applied"
    CONFLICT = "conflict"
    REJECTED = "rejected"


def compute_checksum(payload: dict) -> str:
    """Compute the canonical SHA-256 checksum of a change payload.

    Must produce byte-identical output to
    :meth:`server.services.sync_service.SyncService.compute_checksum`
    for any given ``payload``, or every push from this client will be
    rejected on arrival.

    Args:
        payload: The JSON-safe payload to checksum.

    Returns:
        A hex-encoded SHA-256 digest of ``payload``'s canonical
        (sorted-key, separator-normalized) JSON encoding.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

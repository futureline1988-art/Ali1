"""The wire-level contract the Attendance Client's sync code must honor.

This module intentionally does not import anything from ``server`` or
``developer_suite``. The Attendance Client, the Attendance Server, and
the Developer Suite communicate only over HTTP; a Python-level import
across any of those boundaries would quietly reintroduce a process
-level coupling the HTTP boundary exists specifically to avoid, and
would additionally pull the entire (large, developer-only)
``developer_suite`` dependency tree into the customer-facing
PyInstaller bundle. Instead, this module *replicates* the two pieces of
the protocol a client must reproduce byte-for-byte to interoperate: the
checksum algorithm and the set of valid operations — the same
replication :mod:`developer_suite.sync.protocol` performs for the
Developer Suite's own side of this exact boundary.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum

__all__ = ["SyncOperation", "DeviceType", "compute_checksum"]


class SyncOperation(str, Enum):
    """Mirrors :class:`server.models.sync.SyncOperation`'s values exactly."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class DeviceType(str, Enum):
    """Mirrors :class:`server.models.device.DeviceType`'s values exactly.

    Sent as the ``device_type`` field of a
    ``POST /api/v1/devices/register`` request body — see
    :func:`~sync.client.register_device`.
    """

    DEVELOPER_SUITE = "developer_suite"
    ATTENDANCE_CLIENT = "attendance_client"


def compute_checksum(payload: dict) -> str:
    """Compute the canonical SHA-256 checksum of a change payload.

    Must produce byte-identical output to
    :meth:`server.services.sync_service.SyncService.compute_checksum`
    for any given ``payload`` — used here only to verify a pulled
    configuration payload's checksum matches what the server recorded,
    never to sign an outgoing push (this client never pushes).

    Args:
        payload: The JSON-safe payload to checksum.

    Returns:
        A hex-encoded SHA-256 digest of ``payload``'s canonical
        (sorted-key, separator-normalized) JSON encoding.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

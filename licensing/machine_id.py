"""Stable, unique per-machine identifier used to bind a license.

Combines a handful of relatively stable local OS/hardware identifiers
into one SHA-256 fingerprint. No single input is fully tamper-proof on
its own (a MAC address can be spoofed, a hostname can be renamed), but
combining several makes casually copying an activated license onto a
different machine immediately detectable — which is this module's
actual goal. Nothing here defends against a determined attacker with
local administrator access; no purely offline check can.
"""

from __future__ import annotations

import hashlib
import platform
import uuid


def _raw_fingerprint_sources() -> list[str]:
    """Collect the raw, unhashed identifiers this machine ID is built from."""
    return [
        platform.node(),  # hostname
        platform.system(),  # "Linux" / "Windows" / "Darwin"
        platform.machine(),  # architecture, e.g. "x86_64"
        f"{uuid.getnode():012x}",  # MAC-address-derived node ID (RFC 4122)
    ]


def get_machine_id() -> str:
    """Compute this machine's stable fingerprint.

    Note:
        ``uuid.getnode()`` falls back to a randomly generated value on
        the rare machine with no discoverable network hardware address
        at all; on such a machine this fingerprint would not be stable
        across process restarts. Every machine with a real (even
        virtual/loopback) network interface is unaffected.

    Returns:
        A 64-character lowercase hex SHA-256 digest, stable across
        repeated calls on the same machine.
    """
    raw = "|".join(_raw_fingerprint_sources())
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def format_machine_id_for_display(machine_id: str) -> str:
    """Format a raw machine ID into readable, dash-separated groups.

    Args:
        machine_id: A raw machine ID as returned by :func:`get_machine_id`.

    Returns:
        The first 32 hex characters of ``machine_id``, grouped into
        4-character blocks and uppercased (e.g. ``"A1B2-C3D4-...-"``\\ )
        for easy reading and transcription by a customer relaying it to
        support.
    """
    truncated = machine_id[:32].upper()
    return "-".join(truncated[i : i + 4] for i in range(0, len(truncated), 4))

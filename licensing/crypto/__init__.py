"""Private-key Ed25519 operations.

Everything in this package operates on raw keys and bytes only — no
JSON, no license-specific format. That keeps the one place
private-key material can flow through this codebase small and
auditable.

Used by the Developer Suite's software-update package signing (see
:mod:`developer_suite.services.update_manager_service`) and verified
by the Attendance Client's update installer (:mod:`updates.verifier`).
Never imported by the Attendance Client itself — it only ever verifies
signatures against an embedded public key.
"""

from __future__ import annotations

from licensing.crypto.signing import (
    generate_keypair,
    load_private_key,
    save_private_key,
    save_public_key,
    sign_bytes,
)

__all__ = [
    "generate_keypair",
    "load_private_key",
    "save_private_key",
    "save_public_key",
    "sign_bytes",
]

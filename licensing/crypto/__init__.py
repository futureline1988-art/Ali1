"""Private-key Ed25519 operations.

Everything in this package operates on raw keys and bytes only — no
JSON, no license key string format, no
:class:`~licensing.license_key.LicensePayload`. That keeps the one
place private-key material can flow through this codebase small and
auditable.

This module is new foundation code added in Phase 1 of the commercial
platform work (see ``docs/PLATFORM_ARCHITECTURE_GAP_ANALYSIS.md``) for
the future Developer Suite application to import. It is not imported
by anything in the running Attendance Client, and
``licensing/license_generator.py`` (the existing, working, vendor-only
CLI tool) is deliberately left untouched rather than migrated onto it
— see this package's own module docstring in ``signing.py`` for why.
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

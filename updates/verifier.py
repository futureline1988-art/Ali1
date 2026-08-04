"""Checksum and digital-signature verification for a downloaded update package.

The one place a downloaded package file is judged safe to install.
Both checks must pass before :mod:`updates.checker` ever marks a
package :attr:`~updates.state.UpdateDownloadStatus.VERIFIED` — the
"never install corrupted packages" requirement is enforced entirely by
callers only ever reaching the install step through that status.
"""

from __future__ import annotations

import hashlib
from base64 import b64decode
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_HASH_CHUNK_SIZE = 1024 * 1024


def compute_sha256(file_path: Path) -> str:
    """Compute a file's SHA-256 hex digest, streaming rather than loading it whole into memory."""
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_checksum(file_path: Path, expected_sha256: str) -> bool:
    """Whether ``file_path``'s actual SHA-256 matches ``expected_sha256``."""
    return compute_sha256(file_path) == expected_sha256


def verify_signature(file_path: Path, signature_base64: str, public_key: Ed25519PublicKey) -> bool:
    """Whether ``file_path``'s contents are genuinely signed by the holder of ``public_key``.

    Args:
        file_path: The downloaded package file.
        signature_base64: The base64-encoded Ed25519 signature the
            server returned alongside the package (see
            :mod:`updates.client`).
        public_key: The embedded update-signing public key (see
            :mod:`updates.keys`).

    Returns:
        ``True`` if the signature verifies; ``False`` for any
        verification failure (malformed base64, wrong signature) —
        never raises, so a caller can treat this exactly like
        :func:`verify_checksum`.
    """
    try:
        signature = b64decode(signature_base64)
    except Exception:  # noqa: BLE001 - any malformed input means "not verified"
        return False
    try:
        public_key.verify(signature, file_path.read_bytes())
    except InvalidSignature:
        return False
    return True

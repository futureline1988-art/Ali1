"""Ed25519 keypair generation, loading, and raw byte signing.

Phase 1 foundation code: this is new, and nothing in the currently
shipping Attendance Client imports it. It exists for the future
Developer Suite application (which will hold the private key,
encrypted at rest, and use this module to sign license payloads) and
is written and tested standalone in this phase.

Deliberately **not** wired into ``licensing/license_generator.py``
(the existing, working, vendor-only CLI script) in this phase: that
script already has its own equivalent, tested, in-production logic for
generating a keypair and loading/using a private key, and the explicit
Phase 1 constraint is to add new library code without touching
anything that already works. Once the Developer Suite (Phase 2)
actually consumes this module, refactoring ``license_generator.py`` to
delegate to it instead of duplicating the logic becomes a reasonable,
low-risk cleanup — but that is a decision for whoever approves that
phase, not made here.
"""

from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import load_pem_private_key


class SigningKeyError(Exception):
    """Base class for private-key load/save failures."""


class InvalidPrivateKeyError(SigningKeyError):
    """A loaded PEM does not contain an Ed25519 private key."""


def generate_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Generate a new Ed25519 signing keypair, in memory only.

    Callers that also need the keys on disk should follow up with
    :func:`save_private_key`/:func:`save_public_key`; this function
    itself performs no file I/O, so it is equally usable for
    in-memory-only tests.

    Returns:
        The ``(private_key, public_key)`` pair.
    """
    private_key = Ed25519PrivateKey.generate()
    return private_key, private_key.public_key()


def save_private_key(
    private_key: Ed25519PrivateKey,
    path: Path,
    *,
    password: bytes | None = None,
) -> None:
    """Write ``private_key`` to ``path`` as PEM, creating parent directories.

    Args:
        private_key: The key to serialize.
        path: Destination file. Keep this out of version control and
            off of any machine other than the one running the
            Developer Suite.
        password: If given, encrypts the PEM with this passphrase
            (:class:`~cryptography.hazmat.primitives.serialization.BestAvailableEncryption`).
            ``None`` writes an unencrypted PEM — the caller is
            responsible for protecting the file itself in that case
            (e.g. restrictive filesystem permissions).
    """
    encryption: serialization.KeySerializationEncryption
    encryption = (
        serialization.BestAvailableEncryption(password)
        if password
        else serialization.NoEncryption()
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=encryption,
        )
    )


def save_public_key(public_key: Ed25519PublicKey, path: Path) -> None:
    """Write ``public_key`` to ``path`` as PEM, creating parent directories.

    Args:
        public_key: The key to serialize.
        path: Destination file. Safe to distribute/commit — this is
            what belongs in ``licensing/keys.py``'s ``PUBLIC_KEY_PEM``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )


def load_private_key(path: Path, *, password: bytes | None = None) -> Ed25519PrivateKey:
    """Load and validate an Ed25519 private key from a PEM file.

    Args:
        path: Path to a PEM-encoded private key, as written by
            :func:`save_private_key`.
        password: The passphrase it was encrypted with, if any.

    Returns:
        The loaded private key.

    Raises:
        InvalidPrivateKeyError: The file does not contain an Ed25519
            private key (wrong key type, or not a private key at all).
    """
    try:
        key = load_pem_private_key(path.read_bytes(), password=password)
    except ValueError as exc:
        # Raised directly by load_pem_private_key for PEM that parses but
        # isn't a private key at all (e.g. a public key file) - not just
        # for a private key of the wrong type, which is caught below.
        raise InvalidPrivateKeyError(f"{path} does not contain a private key: {exc}") from exc

    if not isinstance(key, Ed25519PrivateKey):
        raise InvalidPrivateKeyError(
            f"{path} does not contain an Ed25519 private key (got {type(key).__name__})."
        )
    return key


def sign_bytes(private_key: Ed25519PrivateKey, data: bytes) -> bytes:
    """Sign ``data`` with ``private_key``.

    A thin wrapper over :meth:`Ed25519PrivateKey.sign` — its only
    purpose is to be the one call site in this codebase (outside
    ``license_generator.py``'s own independent implementation) where a
    private key actually produces a signature, so that boundary is
    trivial to audit.

    Args:
        private_key: The signing key.
        data: The exact bytes to sign.

    Returns:
        The raw Ed25519 signature bytes.
    """
    return private_key.sign(data)

"""Ed25519 keypair generation, loading, and raw byte signing.

Originally Phase 1 foundation code, written standalone before the
Developer Suite existed to consume it. It is now the module both of
the Developer Suite's own signing services use:
:mod:`developer_suite.services.license_service` (holds the license
-signing key) and :mod:`developer_suite.services.update_manager_service`
(holds the separate update-signing key) — see :func:`ensure_keypair`
for the auto-bootstrap entry point both call. The Attendance Client
still never imports this module: it only ever verifies signatures
against an embedded *public* key (``licensing/keys.py``,
``updates/keys.py``), never holds a private key at all.

``licensing/license_generator.py`` (the offline, vendor-only CLI
script this module was originally kept separate from) still has its
own independent, equivalent logic for generating a keypair and issuing
a key from the command line — that duplication is deliberate and
unchanged; the two entry points serve different callers (an operator
at a terminal vs. the running Developer Suite application) and neither
needs to depend on the other.
"""

from __future__ import annotations

import os
import tempfile
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


def _write_bytes_exclusive(path: Path, data: bytes) -> None:
    """Create ``path`` with ``data``, atomically, failing if it already exists.

    The building block :func:`ensure_keypair` uses instead of
    :func:`save_private_key`/:func:`save_public_key`'s plain
    ``Path.write_bytes`` (which always overwrites). Two things must
    both hold for this to be safe against two Developer Suite processes
    racing to bootstrap the same missing key at once:

    1. **Exclusive creation** — the destination must never be silently
       replaced if it already exists (closing the "check it's missing,
       then write it" TOCTOU window a plain ``exists()`` check would
       leave open).
    2. **No partially-written file ever becomes visible at the
       destination path** — a naive ``open(path, O_CREAT | O_EXCL)``
       followed by a separate ``write()`` satisfies (1) but not (2):
       the instant ``open()`` succeeds, the (still-empty) file exists
       at ``path`` under that name, so a second, losing caller's
       :func:`load_private_key` can — and, tested under real
       contention, does — read it mid-write and blow up on malformed
       PEM framing, rather than cleanly losing the race and loading
       the winner's finished key.

    The write-elsewhere-then-link-into-place pattern below satisfies
    both: the full content is written to and fsync'd on a private
    temporary file first (never visible under ``path``'s name), and
    only then does ``os.link`` — a single atomic filesystem operation
    that fails with :class:`FileExistsError` rather than replacing an
    existing target — make it appear at ``path``, fully-formed, with
    no in-between state anyone else can observe.

    Raises:
        FileExistsError: ``path`` already exists.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.link(tmp_name, path)
    finally:
        os.unlink(tmp_name)


def ensure_keypair(
    private_key_path: Path,
    *,
    public_key_path: Path | None = None,
    password: bytes | None = None,
) -> Ed25519PrivateKey:
    """Load the signing key at ``private_key_path``, generating it once if it is missing.

    The auto-bootstrap counterpart to :func:`load_private_key`: a fresh
    install of the application that holds this key (today, only the
    Developer Suite — see
    :meth:`developer_suite.services.license_service.LicenseService._load_private_key`
    and
    :meth:`developer_suite.services.update_manager_service.UpdateManagerService._load_private_key`)
    has nowhere to get a signing key from except generating its own the
    first time one is actually needed. Nothing here changes the key
    format, the signing algorithm, or how a key is verified —
    :func:`generate_keypair`/:func:`sign_bytes` and this file's PEM
    encoding are exactly what already shipped; this function only
    decides *when* a keypair gets created.

    Safety properties, in order of how they're achieved:

    * **Never overwrites an existing key.** If ``private_key_path``
      already exists, it is loaded and returned as-is — generation is
      skipped entirely, even if the file turns out to be invalid (see
      below). A key that has already signed a real license must never
      be silently replaced.
    * **A corrupt-but-present key is never treated as "missing".** If
      the file exists but does not contain a valid Ed25519 private key,
      :func:`load_private_key` raises :class:`InvalidPrivateKeyError`
      (a :class:`SigningKeyError`) — this propagates rather than
      triggering generation, so a damaged key file surfaces as an error
      for a human to investigate instead of quietly being replaced.
    * **Race-safe.** Two processes (or threads) calling this
      concurrently against the same missing path can't both "win":
      :func:`_write_bytes_exclusive` uses ``O_CREAT | O_EXCL``, so only
      one generation actually lands on disk; the loser's
      :class:`FileExistsError` is caught here and turned into loading
      whatever the winner just wrote, so every caller ends up returning
      the *same* keypair rather than two callers each holding a
      different, mutually-invalid one.

    Args:
        private_key_path: Where the signing private key lives (or
            should be created).
        public_key_path: If given and a new keypair is generated, the
            matching public key is also written here (creation only,
            same exclusive-write safety — never overwritten either).
            Purely a convenience for retrieving the public half to
            embed in a future build's ``PUBLIC_KEY_PEM``; omit if the
            caller has no use for it. Ignored entirely when
            ``private_key_path`` already exists.
        password: Passphrase to decrypt an existing key with, or to
            encrypt a newly generated one with; ``None`` for an
            unencrypted PEM either way.

    Returns:
        The private key — either freshly generated, or already present
        on disk.

    Raises:
        InvalidPrivateKeyError: ``private_key_path`` exists but does
            not contain a valid Ed25519 private key.
    """
    try:
        return load_private_key(private_key_path, password=password)
    except FileNotFoundError:
        pass

    private_key, public_key = generate_keypair()
    encryption: serialization.KeySerializationEncryption = (
        serialization.BestAvailableEncryption(password) if password else serialization.NoEncryption()
    )
    private_key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    )
    try:
        _write_bytes_exclusive(private_key_path, private_key_bytes)
    except FileExistsError:
        # Lost the race to another process/thread generating the same
        # key concurrently -- load THEIRS rather than returning a
        # second, different keypair nothing on disk actually matches.
        return load_private_key(private_key_path, password=password)

    if public_key_path is not None:
        public_key_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        try:
            _write_bytes_exclusive(public_key_path, public_key_bytes)
        except FileExistsError:
            # A stray leftover public key file losing this race is
            # harmless -- the private key file above is the single
            # source of truth this function guarantees.
            pass

    return private_key


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

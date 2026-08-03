"""At-rest encryption for the Developer Suite's own sensitive columns.

Mirrors ``utils.encryption``'s design (Fernet/AES-128-CBC + HMAC-SHA256,
a per-installation key generated on first use and stored under the
application's own writable data directory) closely enough that the two
modules are interchangeable in shape — but deliberately not reused
directly. ``utils.encryption`` resolves its data directory via
``config.get_config()``, the Attendance Client's own process-wide
config singleton; importing it here would silently give the Developer
Suite's schema a runtime dependency on the Attendance Client's
configuration, which is exactly the coupling the platform's "fully
independent schemas/config/secrets" boundary forbids (see
``developer_suite/database/base.py``'s and ``developer_suite/config.py``'s
own docstrings for the same reasoning applied to the base model and to
``PathsConfig``). This module resolves its key path from
:func:`developer_suite.config.get_developer_suite_config` instead, so
it stays entirely inside this application's own boundary.

Currently backs :class:`~developer_suite.models.encrypted_types.EncryptedString`,
used for :attr:`~developer_suite.models.sync_state.SyncDeviceCredential.api_key`
— this installation's own long-lived Attendance Server sync credential,
which must be recoverable in plaintext to authenticate future push/pull
calls (see that model's docstring), so it cannot use the Attendance
Server's own bcrypt-hash-only pattern.
"""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from developer_suite.config import get_developer_suite_config

_KEY_FILENAME = ".field_encryption.key"

_lock = threading.Lock()
_cached_fernet: Fernet | None = None


class DecryptionError(Exception):
    """Raised when a value cannot be decrypted with the current key.

    Almost always means the encryption key file was lost, replaced, or
    belongs to a different installation.
    """


def _key_path() -> Path:
    """The per-installation key file's path, inside this application's data directory."""
    return get_developer_suite_config().paths.data_dir / _KEY_FILENAME


def _load_or_create_key() -> bytes:
    """Read this installation's encryption key, generating it on first use.

    Returns:
        The raw Fernet key bytes.
    """
    path = _key_path()
    if path.exists():
        return path.read_bytes()

    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with restrictive permissions from the start rather than
    # chmod-ing after the fact, closing the brief window where a
    # default-permission file would otherwise be readable by others.
    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    return key


def _get_fernet() -> Fernet:
    """Return the process-wide :class:`Fernet` instance, creating it once."""
    global _cached_fernet
    with _lock:
        if _cached_fernet is None:
            _cached_fernet = Fernet(_load_or_create_key())
        return _cached_fernet


def reset_cached_key() -> None:
    """Drop the cached :class:`Fernet` instance, forcing a re-read of the key file.

    Test-only: lets a test that switches this process's Developer Suite
    config between isolated ``tmp_path`` data directories avoid one
    test's key file leaking into the next.
    """
    global _cached_fernet
    with _lock:
        _cached_fernet = None


def encrypt_text(plaintext: str) -> str:
    """Encrypt a string for storage in an encrypted column.

    Args:
        plaintext: The value to encrypt.

    Returns:
        A URL-safe, text-safe encrypted token suitable for storing in a
        ``String`` column.
    """
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_text(token: str) -> str:
    """Decrypt a value previously produced by :func:`encrypt_text`.

    Args:
        token: The encrypted token, as read back from the database.

    Returns:
        The original plaintext string.

    Raises:
        DecryptionError: If ``token`` cannot be decrypted with this
            installation's current key.
    """
    try:
        return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise DecryptionError(
            "Could not decrypt a stored value with this installation's "
            "encryption key. The key file may have been lost or replaced."
        ) from exc

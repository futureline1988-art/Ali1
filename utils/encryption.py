"""At-rest encryption for sensitive column values and backup files.

Symmetric (Fernet/AES-128-CBC + HMAC-SHA256) encryption keyed by a
per-installation key generated on first use and stored under this
installation's writable data directory (see ``config.PathsConfig.data_dir``)
-- never embedded in the application itself, unlike
``licensing/keys.py``'s public key. That distinction is deliberate: a
license key only needs to be *verified* by every installation (so its
public half can safely ship in the code every user has), but data
encrypted here must only be *readable* by the one installation that
wrote it, so the key protecting it cannot be something every copy of
the application already knows.

This module intentionally does not attempt whole-database encryption
(that would mean switching SQLite drivers entirely, e.g. to SQLCipher)
or deterministic/searchable encryption (HMAC blind-indexing, to keep a
column both encrypted and equality-queryable). Both are legitimate
follow-ups; this module covers the two things achievable without either:
value-level encryption for columns that are never queried by equality
(see ``models/employee.py``'s ``salary`` and ``models/device.py``'s
``communication_key``, both wrapped in ``models.encrypted_types``) and
whole-file encryption for backups (see ``services/backup_service.py``).
"""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from config import get_config

_KEY_FILENAME = ".field_encryption.key"

_lock = threading.Lock()
_cached_fernet: Fernet | None = None


class DecryptionError(Exception):
    """Raised when a value or file cannot be decrypted with the current key.

    Almost always means the encryption key file was lost, replaced, or
    belongs to a different installation -- there is no way to recover
    the original plaintext without it (by design: that is what makes
    this encryption meaningful rather than obfuscation).
    """


def _key_path() -> Path:
    """The per-installation key file's path, inside the writable data directory."""
    return get_config().paths.data_dir / _KEY_FILENAME


def _load_or_create_key() -> bytes:
    """Read this installation's encryption key, generating it on first use.

    The key file is written with owner-only permissions where the
    platform supports it (POSIX ``0600``); on Windows, protection comes
    from the file already living under the current user's
    ``%LOCALAPPDATA%`` profile (see ``config._resolve_data_root``),
    which other user accounts cannot read by default.

    Returns:
        The raw Fernet key bytes.
    """
    path = _key_path()
    if path.exists():
        return path.read_bytes()

    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    # Create with restrictive permissions from the start rather than
    # chmod-ing after the fact, closing the brief window where a
    # default-permission file would otherwise be readable by others.
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


def encrypt_text(plaintext: str) -> str:
    """Encrypt a string for storage in an encrypted column.

    Args:
        plaintext: The value to encrypt.

    Returns:
        A URL-safe, text-safe encrypted token suitable for storing in
        a ``String`` column.
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
            installation's current key (wrong/lost key, or corrupted
            data).
    """
    try:
        return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise DecryptionError(
            "Could not decrypt a stored value with this installation's "
            "encryption key. The key file may have been lost or replaced."
        ) from exc


def encrypt_file(source_path: Path, destination_path: Path) -> None:
    """Encrypt an entire file's contents into a new file.

    Used for whole-database backup files rather than individual
    values -- see :mod:`services.backup_service`.

    Args:
        source_path: The plaintext file to read.
        destination_path: Where to write the encrypted result.
    """
    token = _get_fernet().encrypt(source_path.read_bytes())
    destination_path.write_bytes(token)


def decrypt_file(source_path: Path, destination_path: Path) -> None:
    """Decrypt a file previously produced by :func:`encrypt_file`.

    Args:
        source_path: The encrypted file to read.
        destination_path: Where to write the decrypted plaintext.

    Raises:
        DecryptionError: If ``source_path`` cannot be decrypted with
            this installation's current key.
    """
    try:
        plaintext = _get_fernet().decrypt(source_path.read_bytes())
    except InvalidToken as exc:
        raise DecryptionError(
            f"Could not decrypt {source_path} with this installation's "
            "encryption key. The key file may have been lost or replaced, "
            "or the backup belongs to a different installation."
        ) from exc
    destination_path.write_bytes(plaintext)

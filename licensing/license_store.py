"""Encrypted local persistence for the activated license.

Storage is intentionally independent of the application's SQLite
database — the license must be checkable before any company, user, or
even the database schema exists — and is encrypted with a key derived
from this machine's fingerprint (see :mod:`licensing.machine_id`), so
copying the raw store file to a different machine produces a file that
simply fails to decrypt there rather than transferring the license.

This is *not* the same defense as the license key's own Ed25519
signature (which proves a vendor-issued key hasn't been tampered
with) — it protects the *locally stored, already-verified* record from
casual copying/editing. A user with enough access to read this
module's source and re-derive the machine-specific key deliberately
could still forge a local record; nothing purely offline can fully
prevent that, which is exactly the gap an online license server (see
:mod:`licensing.license_service`'s docstring) is positioned to close
later without any change to this file's format.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from config import get_config
from licensing.enums import LicenseType
from licensing.machine_id import get_machine_id

_STORE_FILENAME = "license.dat"
_KEY_DOMAIN_TAG = "ams-license-store-v1"


@dataclass(frozen=True)
class StoredLicenseRecord:
    """The currently activated license, as persisted locally.

    Attributes:
        license_type: Which plan is active.
        raw_key: The original vendor-signed key string this record was
            activated from; ``None`` for a self-issued
            :attr:`~licensing.enums.LicenseType.TRIAL` record, which
            has no vendor signature to re-verify.
        machine_id: The machine this record was activated on (see
            module docstring for what this does and does not protect
            against).
        activated_at: When activation happened, in UTC.
        expires_at: When this license stops being valid; ``None`` means
            it never expires.
    """

    license_type: LicenseType
    raw_key: str | None
    machine_id: str
    activated_at: datetime
    expires_at: date | None

    def to_json_dict(self) -> dict[str, object]:
        """Serialize to a JSON-safe dict."""
        return {
            "license_type": self.license_type.value,
            "raw_key": self.raw_key,
            "machine_id": self.machine_id,
            "activated_at": self.activated_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, object]) -> "StoredLicenseRecord":
        """Reconstruct a record from :meth:`to_json_dict`'s output."""
        return cls(
            license_type=LicenseType(data["license_type"]),
            raw_key=data["raw_key"],
            machine_id=str(data["machine_id"]),
            activated_at=datetime.fromisoformat(data["activated_at"]),
            expires_at=date.fromisoformat(data["expires_at"]) if data.get("expires_at") else None,
        )


@dataclass(frozen=True)
class LicenseEnvelope:
    """The full contents of the local license store.

    Attributes:
        trial_used: Whether a trial has ever been started on this
            machine — kept independently of :attr:`current` so that
            activating a paid key afterward (which replaces
            :attr:`current`) does not reset trial eligibility.
        current: The currently active license record, or ``None`` if
            nothing has been activated (or it was explicitly cleared).
    """

    trial_used: bool
    current: StoredLicenseRecord | None

    def to_json_dict(self) -> dict[str, object]:
        """Serialize to a JSON-safe dict."""
        return {
            "trial_used": self.trial_used,
            "current": self.current.to_json_dict() if self.current else None,
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, object]) -> "LicenseEnvelope":
        """Reconstruct an envelope from :meth:`to_json_dict`'s output."""
        current_data = data.get("current")
        return cls(
            trial_used=bool(data.get("trial_used", False)),
            current=StoredLicenseRecord.from_json_dict(current_data) if current_data else None,
        )


class LicenseStore:
    """Reads and writes the encrypted local license envelope."""

    def __init__(self, *, store_path: Path | None = None, machine_id: str | None = None) -> None:
        """Create a license store.

        Args:
            store_path: Override the store file's location; defaults to
                ``<data_dir>/license.dat``. Primarily for tests.
            machine_id: Override the machine fingerprint the encryption
                key is derived from. Primarily for tests.
        """
        self._path = store_path or (get_config().paths.data_dir / _STORE_FILENAME)
        self._machine_id = machine_id or get_machine_id()

    def _fernet(self) -> Fernet:
        """Build a Fernet cipher whose key is derived from this machine's fingerprint."""
        digest = hashlib.sha256(f"{_KEY_DOMAIN_TAG}:{self._machine_id}".encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest))

    def load(self) -> LicenseEnvelope:
        """Read and decrypt the stored envelope.

        Returns:
            The stored envelope, or an empty one (nothing activated,
            trial not used) if the file does not exist, cannot be
            decrypted with this machine's key (e.g. copied from a
            different machine), or is otherwise corrupt. A decrypt
            failure is treated as "no license" rather than raised,
            since a corrupt store should fail the same way an absent
            one does - by prompting re-activation - not by crashing
            the application at startup.
        """
        if not self._path.exists():
            return LicenseEnvelope(trial_used=False, current=None)

        try:
            decrypted = self._fernet().decrypt(self._path.read_bytes())
            data = json.loads(decrypted)
            return LicenseEnvelope.from_json_dict(data)
        except (InvalidToken, ValueError, KeyError, OSError):
            return LicenseEnvelope(trial_used=False, current=None)

    def save(self, envelope: LicenseEnvelope) -> None:
        """Encrypt and persist ``envelope``, creating the parent directory if needed.

        Args:
            envelope: The full envelope to persist (callers should read
                the existing envelope first if they only mean to change
                one part of it - see :meth:`set_current`).
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(envelope.to_json_dict()).encode("utf-8")
        self._path.write_bytes(self._fernet().encrypt(payload))

    def set_current(self, record: StoredLicenseRecord) -> None:
        """Persist ``record`` as the active license, preserving :attr:`trial_used`.

        Args:
            record: The newly activated license record.
        """
        existing = self.load()
        trial_used = existing.trial_used or record.license_type is LicenseType.TRIAL
        self.save(LicenseEnvelope(trial_used=trial_used, current=record))

    def clear_current(self) -> None:
        """Remove the active license record, preserving :attr:`trial_used`."""
        existing = self.load()
        self.save(LicenseEnvelope(trial_used=existing.trial_used, current=None))

    def wipe(self) -> None:
        """Delete the store file entirely, resetting trial eligibility too.

        Primarily for tests; the application itself never calls this
        (there is no in-app "reset trial" action).
        """
        if self._path.exists():
            self._path.unlink()


def utc_now() -> datetime:
    """Return the current UTC time, for stamping :attr:`StoredLicenseRecord.activated_at`."""
    return datetime.now(timezone.utc)

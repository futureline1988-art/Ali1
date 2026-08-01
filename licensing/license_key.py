"""Signed license key encoding and verification.

A license key is a compact, pasteable string of the form
``AMS1.<payload>.<signature>``: a versioned format tag, the license's
JSON payload, and an Ed25519 signature over that payload — both
base64url-encoded (no padding), joined with dots (deliberately
JWT-shaped, since that structure is well understood and easy to eyeball
for "is this even a plausible key").

:func:`encode_license_key` (signing) requires the vendor's private key
and is only ever called by :mod:`licensing.license_generator`, run
offline by the vendor. :func:`decode_and_verify_license_key`
(verification only, needs just the public key from
:mod:`licensing.keys`) is what the running application calls.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import asdict, dataclass
from datetime import date

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from licensing.enums import LicenseType

_FORMAT_TAG = "AMS1"


class LicenseKeyError(Exception):
    """Base class for license key encode/decode failures."""


class MalformedLicenseKeyError(LicenseKeyError):
    """The key string is not shaped like a license key at all."""


class InvalidLicenseSignatureError(LicenseKeyError):
    """The key's signature does not verify against the embedded public key."""


@dataclass(frozen=True)
class LicensePayload:
    """The signed contents of a license key.

    Attributes:
        license_id: A unique identifier for this specific license grant
            (not a secret; useful for support/revocation record-keeping).
        customer_name: Who the license was issued to, for display and
            audit purposes.
        license_type: Which plan this key grants.
        machine_id: If set, this key only verifies successfully on the
            machine with this exact
            :func:`~licensing.machine_id.get_machine_id` fingerprint.
            ``None`` means the key is not pre-locked to a specific
            machine (it binds to whichever machine first activates it —
            see :mod:`licensing.license_store`).
        issued_at: The date this key was generated.
        expires_at: The date this key stops being valid; ``None`` means
            it never expires (:attr:`~licensing.enums.LicenseType.LIFETIME`).
        features: Optional feature-flag codes this key unlocks, for
            future use (empty by default; nothing in this application
            currently branches on it).
    """

    license_id: str
    customer_name: str
    license_type: LicenseType
    machine_id: str | None
    issued_at: date
    expires_at: date | None
    features: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, object]:
        """Serialize to a JSON-safe dict (dates as ISO strings, enum as its value)."""
        data = asdict(self)
        data["license_type"] = self.license_type.value
        data["issued_at"] = self.issued_at.isoformat()
        data["expires_at"] = self.expires_at.isoformat() if self.expires_at else None
        data["features"] = list(self.features)
        return data

    @classmethod
    def from_json_dict(cls, data: dict[str, object]) -> "LicensePayload":
        """Reconstruct a :class:`LicensePayload` from :meth:`to_json_dict`'s output.

        Raises:
            MalformedLicenseKeyError: If a required field is missing or
                has the wrong shape.
        """
        try:
            return cls(
                license_id=str(data["license_id"]),
                customer_name=str(data["customer_name"]),
                license_type=LicenseType(data["license_type"]),
                machine_id=data["machine_id"],
                issued_at=date.fromisoformat(data["issued_at"]),
                expires_at=(
                    date.fromisoformat(data["expires_at"]) if data.get("expires_at") else None
                ),
                features=tuple(data.get("features") or ()),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MalformedLicenseKeyError(f"Malformed license payload: {exc}") from exc


def _b64url_encode(raw: bytes) -> str:
    """Base64url-encode ``raw`` without padding."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    """Reverse :func:`_b64url_encode`, restoring the padding it stripped."""
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def encode_license_key(payload: LicensePayload, private_key: Ed25519PrivateKey) -> str:
    """Sign ``payload`` and encode it as a pasteable license key string.

    Vendor-side only: requires the private key, which never ships with
    the application.

    Args:
        payload: The license grant to encode.
        private_key: The vendor's Ed25519 private key.

    Returns:
        A ``AMS1.<payload>.<signature>`` license key string.
    """
    payload_bytes = json.dumps(payload.to_json_dict(), separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    signature = private_key.sign(payload_bytes)
    return f"{_FORMAT_TAG}.{_b64url_encode(payload_bytes)}.{_b64url_encode(signature)}"


def decode_and_verify_license_key(
    license_key: str, public_key: Ed25519PublicKey
) -> LicensePayload:
    """Verify a license key's signature and decode its payload.

    Args:
        license_key: A key string as produced by :func:`encode_license_key`.
        public_key: The application's embedded public key (see
            :func:`licensing.keys.load_public_key`).

    Returns:
        The verified :class:`LicensePayload`.

    Raises:
        MalformedLicenseKeyError: If ``license_key`` is not shaped like
            a license key, or its payload does not decode to valid JSON
            with the expected fields.
        InvalidLicenseSignatureError: If the signature does not verify
            against ``public_key`` (wrong key, corrupted key, or a
            forged/tampered key).
    """
    stripped = license_key.strip()
    parts = stripped.split(".")
    if len(parts) != 3 or parts[0] != _FORMAT_TAG:
        raise MalformedLicenseKeyError(
            "License key must have the form 'AMS1.<payload>.<signature>'."
        )
    _tag, payload_part, signature_part = parts

    try:
        payload_bytes = _b64url_decode(payload_part)
        signature = _b64url_decode(signature_part)
    except (ValueError, binascii.Error) as exc:
        raise MalformedLicenseKeyError(f"License key is not valid base64: {exc}") from exc

    try:
        public_key.verify(signature, payload_bytes)
    except InvalidSignature as exc:
        raise InvalidLicenseSignatureError(
            "This license key's signature does not match - it was not issued for "
            "this application, or has been tampered with."
        ) from exc

    try:
        payload_dict = json.loads(payload_bytes)
    except json.JSONDecodeError as exc:
        raise MalformedLicenseKeyError(f"License key payload is not valid JSON: {exc}") from exc

    return LicensePayload.from_json_dict(payload_dict)

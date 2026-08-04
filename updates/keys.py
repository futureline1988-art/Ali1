"""The Attendance Client's embedded update-package-verification public key.

Mirrors ``licensing/keys.py``'s exact shape and reasoning, deliberately
duplicated rather than imported — this phase's own explicit "do not
change licensing" constraint means nothing under ``licensing/`` may be
touched, and reusing ``licensing/keys.py``'s constant directly would
also be a security mistake even without that constraint: a license key
and an update package answer completely different questions (see
:mod:`developer_suite.services.update_manager_service`'s own
docstring), so they must never share a keypair. Only the *public* key
ships here — it can verify a signature but cannot produce one. The
matching private key is held solely by the vendor, configured as
:attr:`~developer_suite.config.DeveloperSuiteConfig.update_signing_private_key_path`,
and used by :class:`~developer_suite.services.update_manager_service.UpdateManagerService`
to sign every uploaded package.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

PUBLIC_KEY_PEM: bytes = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA68tZXt6yQRyAfiAeFiQLN6rsL5wNibMck2pVOZ2Mmhw=
-----END PUBLIC KEY-----
"""
"""PEM-encoded Ed25519 public key, generated once for this application.

Regenerating this keypair immediately invalidates every update package
signed under the previous one — only do so as a deliberate, coordinated
re-key, never casually (see ``licensing/keys.py``'s own identical
warning for its unrelated keypair).
"""


def load_public_key() -> Ed25519PublicKey:
    """Parse :data:`PUBLIC_KEY_PEM` into a usable key object.

    Returns:
        This application's Ed25519 update-signing public key, for
        signature verification only.
    """
    key = load_pem_public_key(PUBLIC_KEY_PEM)
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError(
            f"Expected an Ed25519 public key, got {type(key).__name__}. "
            "PUBLIC_KEY_PEM is misconfigured."
        )
    return key

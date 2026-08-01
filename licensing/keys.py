"""The application's embedded license-verification public key.

Only the *public* key ships with the application — it can verify a
signature but cannot produce one. The matching private key is held
solely by the vendor (see ``licensing/vendor/private_key.pem``, which
is deliberately excluded from version control by ``.gitignore``) and is
used offline by :mod:`licensing.license_generator` to issue license
keys. This asymmetric split is what makes a license key unforgeable
even though every line of this application's verification code is
visible to anyone who has installed it: knowing exactly how a
signature is checked does not let you produce a new valid one without
the private key.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PUBLIC_KEY_PEM: bytes = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAfYj4nJ9RfKy/99IW2s2oBzDxaNr6Hp+MVo/IwY87fPs=
-----END PUBLIC KEY-----
"""
"""PEM-encoded Ed25519 public key, generated once for this application.

Regenerating this keypair (see ``licensing/license_generator.py
--generate-keypair``) immediately invalidates every license key issued
under the previous one - only do so as a deliberate, coordinated
re-key, never casually.
"""


def load_public_key() -> Ed25519PublicKey:
    """Parse :data:`PUBLIC_KEY_PEM` into a usable key object.

    Returns:
        The application's Ed25519 public key, for signature
        verification only.
    """
    key = load_pem_public_key(PUBLIC_KEY_PEM)
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError(
            f"Expected an Ed25519 public key, got {type(key).__name__}. "
            "PUBLIC_KEY_PEM is misconfigured."
        )
    return key

"""SQLAlchemy column types that transparently encrypt sensitive values at rest.

Mirrors ``models.encrypted_types``'s shape exactly, delegating to
:mod:`developer_suite.security.field_encryption` instead of
``utils.encryption`` — see that module's docstring for why a direct
reuse would have been the wrong kind of reuse here (it would couple
this application's schema to the Attendance Client's own config
singleton).

Same restriction applies as the Attendance Client's version: safe only
for columns never used in a ``WHERE``/``JOIN`` equality comparison and
never covered by a uniqueness constraint, since Fernet encryption is
randomized and the same plaintext produces different ciphertext on
every write.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from developer_suite.security.field_encryption import decrypt_text, encrypt_text


class EncryptedString(TypeDecorator):
    """A ``String`` column whose value is encrypted at rest.

    Currently applied to
    :attr:`~developer_suite.models.sync_state.SyncDeviceCredential.api_key`.
    """

    impl = String(512)
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        """Encrypt an outgoing plaintext value before it reaches the DBAPI."""
        if value is None:
            return None
        return encrypt_text(value)

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        """Decrypt a value coming back from the DBAPI into plaintext."""
        if value is None:
            return None
        return decrypt_text(value)

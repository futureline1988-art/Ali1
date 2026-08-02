"""SQLAlchemy column types that transparently encrypt sensitive values at rest.

Delegates the actual cryptography to :mod:`utils.encryption` (a
per-installation Fernet key, never embedded in the application) and
only handles the ORM-facing plumbing here: converting a value to and
from the encrypted text stored in the database column.

Deliberately NOT applied to every string-ish column, or even every
"sensitive-sounding" one — only to columns that are never looked up by
equality and carry no uniqueness constraint (see each type's docstring
below for the specific reasoning). Fernet encryption is randomized (a
fresh IV every call), so the same plaintext produces different
ciphertext each time it is written; a column encrypted with these types
can never be the target of a ``WHERE column = value`` query or a
database-level ``UNIQUE`` constraint, both of which would silently stop
matching anything. :class:`~models.employee.Employee`'s
``national_id`` is the clearest example of a column that looks like an
encryption candidate but is *not* one here — it is both queried by
:meth:`~repositories.employee_repository.EmployeeRepository.get_by_national_id`
and covered by a ``UNIQUE(company_id, national_id)`` constraint;
encrypting it would require deterministic or blind-indexed encryption
(storing a separate searchable HMAC alongside the encrypted value),
which is a real, larger follow-up left for when it's actually needed
rather than done half-way here.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from utils.encryption import decrypt_text, encrypt_text


class EncryptedString(TypeDecorator):
    """A ``String`` column whose value is encrypted at rest.

    Safe only for columns never used in a ``WHERE``/``JOIN`` equality
    comparison and never covered by a uniqueness constraint — see this
    module's docstring. Currently applied to
    :attr:`~models.device.Device.communication_key`.
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


class EncryptedDecimal(TypeDecorator):
    """A ``Numeric``-like column whose value is encrypted at rest as text.

    Stores the ``Decimal``'s exact string representation (so no
    precision is lost the way a ``float`` round-trip could), encrypted.
    Same restriction as :class:`EncryptedString`: never queryable by
    comparison, ``ORDER BY``, or aggregate at the SQL level, since the
    stored value is opaque ciphertext, not a numeric column type.
    Currently applied to :attr:`~models.employee.Employee.salary`,
    which this codebase never queries, sorts, or aggregates in SQL
    (confirmed - the only reads of it are ``employee.salary`` attribute
    access, always after the ORM has already decrypted it).
    """

    impl = String(512)
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Any) -> str | None:
        """Encrypt an outgoing ``Decimal``'s exact string form before storage."""
        if value is None:
            return None
        return encrypt_text(str(value))

    def process_result_value(self, value: str | None, dialect: Any) -> Decimal | None:
        """Decrypt a stored value and reconstruct the original ``Decimal``."""
        if value is None:
            return None
        decrypted = decrypt_text(value)
        try:
            return Decimal(decrypted)
        except InvalidOperation as exc:
            raise ValueError(f"Decrypted value {decrypted!r} is not a valid Decimal.") from exc

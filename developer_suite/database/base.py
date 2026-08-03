"""Declarative base for the Developer Suite's own schema.

Empty in Phase 2 — no platform administration tables (Customer,
IssuedLicense, PlatformSettings, AuditLog, RemoteConfigProfile, Update
metadata) are defined yet. A later phase adds model modules here that
import and subclass :class:`Base`, exactly matching the pattern
``models/base.py`` establishes for the Attendance Client — deliberately
not the *same* ``Base``, since these are two separate schemas that must
never share one metadata namespace.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Same naming convention as models/base.py, for the same reason: stable,
# dialect-independent constraint/index names if/when this schema is ever
# put under Alembic.
_NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

_metadata = MetaData(naming_convention=_NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Declarative base for every Developer Suite platform-administration model.

    Empty in Phase 2. Future platform-administration models (Customer,
    IssuedLicense, ...) subclass this — never ``models.base.Base``,
    which belongs to the Attendance Client's schema.
    """

    metadata = _metadata

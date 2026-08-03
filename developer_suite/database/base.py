"""Declarative base and base model for the Developer Suite's own schema.

:class:`Base` is a separate SQLAlchemy declarative base from
``models.base.Base`` — these are two schemas that must never share one
metadata namespace, per the platform's ownership boundary (see
``developer_suite/__init__.py``).

:class:`DeveloperSuiteBaseModel` reuses five mixins directly from
``models.base`` unmodified (:class:`~models.base.IdMixin`,
:class:`~models.base.UUIDMixin`, :class:`~models.base.TimestampMixin`,
:class:`~models.base.SoftDeleteMixin`, :class:`~models.base.TableNameMixin`)
rather than re-implementing surrogate-key/UUID/timestamp/soft-delete
columns from scratch — those mixins are plain column-adding classes
with no dependency on ``models.base.Base`` itself, so they compose
cleanly with this package's own ``Base``. This is the "avoid duplicate
implementations" platform rule applied concretely: the *behavior* of a
soft-deletable, timestamped, UUID-bearing row exists in exactly one
place in this codebase.

Phase 8 adds :class:`~models.base.SerializationMixin` to that
composition, for the same reason ``server/database/base.py`` added it
in Phase 7: a model that participates in synchronization needs a
generic, reflection-based ``to_dict()`` to build its outbound sync
payload from (see :mod:`developer_suite.services.customer_service`).
Purely additive — every model built before Phase 8 keeps working
unchanged.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

from models.base import (
    IdMixin,
    SerializationMixin,
    SoftDeleteMixin,
    TableNameMixin,
    TimestampMixin,
    UUIDMixin,
)

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

    Future platform-administration models (Customer, IssuedLicense,
    ...) subclass :class:`DeveloperSuiteBaseModel`, defined below —
    never ``models.base.Base``/``BaseModel``, which belong to the
    Attendance Client's schema.
    """

    metadata = _metadata


class DeveloperSuiteBaseModel(
    TableNameMixin,
    SerializationMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDMixin,
    IdMixin,
    Base,
):
    """Abstract base every concrete Developer Suite model inherits from.

    Combines automatic table naming, an integer primary key, a public
    UUID, created/updated timestamps, soft delete, and generic
    ``to_dict``/``update_from_dict`` (see
    :class:`~models.base.SerializationMixin`) — the same composition
    ``models.base.BaseModel`` uses for the Attendance Client, minus the
    two mixins (``VersionMixin``, ``AuditMixin``) nothing here needs
    yet. A later phase can add them the same way, without touching any
    existing column.

    Example:
        >>> class Customer(DeveloperSuiteBaseModel):
        ...     company_name: Mapped[str] = mapped_column(String(200))
    """

    __abstract__ = True

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return f"<{self.__class__.__name__} id={getattr(self, 'id', None)!r}>"

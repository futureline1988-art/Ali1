"""Declarative base and base model for the Attendance Server's own schema.

:class:`Base` is a third, independent SQLAlchemy declarative base —
alongside ``models.base.Base`` (Attendance Client) and
``developer_suite.database.base.Base`` (Developer Suite) — three
schemas that must never share one metadata namespace, per the
platform's ownership boundary (see this package's parent
``server/__init__.py``).

:class:`ServerBaseModel` reuses the same four mixins directly from
``models.base`` that both other schemas already reuse
(:class:`~models.base.IdMixin`, :class:`~models.base.UUIDMixin`,
:class:`~models.base.TimestampMixin`, :class:`~models.base.SoftDeleteMixin`,
:class:`~models.base.TableNameMixin`) — they have no dependency on
``models.base.Base`` itself, so they compose cleanly with a third,
independent ``Base`` exactly as they already do with
``developer_suite.database.base.Base``. This is the "avoid duplicate
implementations" platform rule applied a second time: the behavior of
a soft-deletable, timestamped, UUID-bearing row still exists in
exactly one place in this codebase.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

from models.base import IdMixin, SoftDeleteMixin, TableNameMixin, TimestampMixin, UUIDMixin

# Same naming convention as models/base.py and developer_suite/database/base.py,
# for the same reason: stable, dialect-independent constraint/index
# names if/when this schema is ever put under Alembic.
_NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

_metadata = MetaData(naming_convention=_NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Declarative base for every Attendance Server model.

    Future Attendance Server models (a customer registry, license
    metadata, remote configuration, ...) subclass
    :class:`ServerBaseModel`, defined below — never
    ``models.base.Base``/``BaseModel`` or
    ``developer_suite.database.base.Base``/``DeveloperSuiteBaseModel``,
    which belong to the other two applications' schemas.
    """

    metadata = _metadata


class ServerBaseModel(
    TableNameMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDMixin,
    IdMixin,
    Base,
):
    """Abstract base every concrete Attendance Server model inherits from.

    Combines automatic table naming, an integer primary key, a public
    UUID, created/updated timestamps, and soft delete — the same
    composition ``models.base.BaseModel`` and
    ``developer_suite.database.base.DeveloperSuiteBaseModel`` use.

    Example:
        >>> class SomeFutureModel(ServerBaseModel):
        ...     name: Mapped[str] = mapped_column(String(200))
    """

    __abstract__ = True

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return f"<{self.__class__.__name__} id={getattr(self, 'id', None)!r}>"

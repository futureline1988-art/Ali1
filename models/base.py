"""Declarative base and reusable mixins for every ORM model in the system.

This module is the single foundation every domain model (``User``,
``Employee``, ``Attendance``, ...) inherits from. It provides:

* :class:`Base` — the SQLAlchemy 2.x declarative base, configured with a
  naming convention so Alembic can autogenerate stable, predictable
  constraint/index names.
* :class:`UTCDateTime` — a cross-dialect column type that guarantees every
  timestamp is stored and read back as timezone-aware UTC, regardless of
  whether the backend is SQLite, PostgreSQL or MySQL.
* A set of composable mixins (:class:`TimestampMixin`,
  :class:`SoftDeleteMixin`, :class:`AuditMixin`, :class:`UUIDMixin`,
  :class:`VersionMixin`) that concrete models get for free by inheriting
  :class:`BaseModel`, so no model file has to redeclare bookkeeping
  columns or serialization logic.
* :class:`CompanyScopedMixin` — an opt-in mixin (not part of
  :class:`BaseModel`) adding the ``company_id`` foreign key that every
  tenant-owned table needs for the multi-company architecture.

Only :class:`BaseModel` (and, where a model genuinely needs to opt out of
part of this behaviour, the individual mixins) should be imported by
domain model modules under ``models/``.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, ClassVar, Iterable, Mapping

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, MetaData, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column
from sqlalchemy.types import TypeDecorator

# ---------------------------------------------------------------------------
# Naming convention: gives every constraint/index a deterministic, dialect
# -independent name so Alembic autogenerate produces stable migrations
# instead of opaque, backend-generated names such as "sqlite_autoindex_1".
# ---------------------------------------------------------------------------
_NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

_metadata = MetaData(naming_convention=_NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model in the application.

    All model modules must subclass :class:`BaseModel` (defined below),
    never this class directly, so that timestamps, soft-delete, audit and
    versioning behaviour stay consistent across the whole schema.
    """

    metadata = _metadata


class UTCDateTime(TypeDecorator):
    """A ``DateTime`` type that is always timezone-aware UTC in Python.

    SQLite has no native timezone-aware storage: it silently drops
    ``tzinfo`` on write and returns naive ``datetime`` objects on read,
    which would make application code behave differently depending on the
    configured database backend. This type decorator normalizes that
    difference:

    * On write, any incoming ``datetime`` (naive or aware) is converted to
      UTC. For SQLite, the ``tzinfo`` is then stripped before storage
      (SQLite would otherwise store it as an opaque string suffix) while
      PostgreSQL/MySQL keep a proper timezone-aware column.
    * On read, a naive value coming back from SQLite is re-attached with
      UTC ``tzinfo`` so callers always receive an aware ``datetime``,
      identical across every supported backend.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Any
    ) -> datetime | None:
        """Normalize an outgoing value to UTC before it reaches the DBAPI."""
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(
        self, value: datetime | None, dialect: Any
    ) -> datetime | None:
        """Ensure a value coming back from the DBAPI is UTC-aware."""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def _camel_to_snake(name: str) -> str:
    """Convert a ``PascalCase``/``CamelCase`` identifier to ``snake_case``."""
    step1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", step1).lower()


def _pluralize(word: str) -> str:
    """Pluralize a snake_case word using common, unambiguous English rules."""
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return f"{word}es"
    if len(word) > 1 and word.endswith("y") and word[-2] not in "aeiou":
        return f"{word[:-1]}ies"
    return f"{word}s"


def enum_column_type(enum_cls: type[Enum], *, length: int = 32) -> SAEnum:
    """Build a portable, value-based SQLAlchemy ``Enum`` type.

    Every enum-backed column in this project should use this factory
    instead of passing a Python ``Enum`` class to ``mapped_column``
    directly, for two reasons:

    * ``values_callable`` is set so the column stores each member's
      ``.value`` (e.g. ``"hr"``) rather than its ``.name`` (``"HR"``),
      matching how the enum is used everywhere else (JSON, comparisons).
    * ``native_enum=False`` makes SQLAlchemy emit a plain ``VARCHAR`` with
      a ``CHECK`` constraint on every backend instead of a native
      PostgreSQL ``ENUM`` type, so adding a new member later is a normal
      data migration instead of an ``ALTER TYPE`` schema change.

    Args:
        enum_cls: The enum class (typically a :class:`~models.enums.BilingualEnum`
            subclass) backing the column.
        length: Maximum stored string length; must comfortably fit the
            longest member value.

    Returns:
        A configured :class:`sqlalchemy.Enum` instance ready to pass to
        ``mapped_column``.
    """
    return SAEnum(
        enum_cls,
        name=f"{_camel_to_snake(enum_cls.__name__)}_enum",
        values_callable=lambda cls: [member.value for member in cls],
        native_enum=False,
        length=length,
    )


class TableNameMixin:
    """Derives ``__tablename__`` automatically from the class name.

    ``Employee`` -> ``employees``, ``CompanySettings`` ->
    ``company_settings``. A concrete model may still override
    ``__tablename__`` explicitly when the automatic name is unsuitable.
    """

    @declared_attr.directive
    def __tablename__(cls) -> str:  # noqa: N805 - SQLAlchemy convention
        return _pluralize(_camel_to_snake(cls.__name__))


class IdMixin:
    """Adds a surrogate auto-incrementing integer primary key."""

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, sort_order=-100
    )


class UUIDMixin:
    """Adds a globally-unique, externally-safe identifier column.

    ``public_id`` is generated automatically and is what should be
    exposed through APIs, QR codes and barcodes instead of the internal
    auto-incrementing ``id``, which must never leak sequential business
    volume (e.g. total employee count) to the outside world.
    """

    public_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        default=uuid.uuid4,
        unique=True,
        index=True,
        nullable=False,
        sort_order=-90,
    )


class CompanyScopedMixin:
    """Adds the mandatory ``company_id`` foreign key for tenant data.

    Any model representing data owned by exactly one company — branches,
    departments, employees, users, roles, devices, attendance, and so on
    — must inherit this mixin *in addition to* :class:`BaseModel`, e.g.
    ``class Employee(CompanyScopedMixin, BaseModel): ...``. It is
    deliberately not part of :class:`BaseModel` itself, because a handful
    of models (:class:`~models.company.Company` — the tenant root — and
    the global :class:`~models.permission.Permission` catalog) must never
    carry a ``company_id``.

    This FK has no ``ON DELETE`` clause, so it defaults to
    ``RESTRICT``/``NO ACTION`` on every supported backend: a
    :class:`~models.company.Company` with any dependent rows cannot be
    deleted at the database level. Enforcing a deliberate "deactivate,
    don't delete" tenant offboarding policy is a service-layer concern.

    Every repository query against a company-scoped model must filter by
    ``company_id == current_company_id`` — this mixin only makes that
    filtering *possible*; it does not enforce it automatically.
    """

    company_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("companies.id"),
        nullable=False,
        index=True,
        sort_order=-80,
    )


class TimestampMixin:
    """Adds ``created_at`` / ``updated_at`` UTC timestamps.

    Both columns are maintained automatically: ``created_at`` is set once
    on insert, ``updated_at`` is refreshed by the ORM on every flush that
    changes the row (see :meth:`_touch_updated_at`).
    """

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=_utc_now, nullable=False, sort_order=90
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=_utc_now,
        onupdate=_utc_now,
        nullable=False,
        sort_order=91,
    )


class SoftDeleteMixin:
    """Adds soft-delete support instead of destructive row removal.

    Enterprise attendance data (employee records, attendance logs) must
    remain auditable even after a user "deletes" it, so deletion is
    modeled as a flag rather than a ``DELETE`` statement. Repositories are
    expected to filter ``is_deleted == False`` by default.
    """

    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True, sort_order=92
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, default=None, nullable=True, sort_order=93
    )

    def soft_delete(self) -> None:
        """Mark this record as deleted without removing it from storage."""
        self.is_deleted = True
        self.deleted_at = _utc_now()

    def restore(self) -> None:
        """Reverse a previous :meth:`soft_delete` call."""
        self.is_deleted = False
        self.deleted_at = None


class AuditMixin:
    """Adds ``created_by`` / ``updated_by`` user attribution.

    Both columns reference ``users.id`` and are nullable so that
    system-initiated changes (migrations, device sync jobs) do not
    require a fabricated user. The referenced row is never deleted when a
    user is removed — the foreign key uses ``ON DELETE SET NULL`` so
    historical audit trails survive user deletion.
    """

    created_by_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        sort_order=94,
    )
    updated_by_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        sort_order=95,
    )


class VersionMixin:
    """Adds optimistic-locking support via a row ``version`` counter.

    SQLAlchemy increments ``version`` automatically on every ``UPDATE``
    and includes the previous value in the ``WHERE`` clause. If two
    threads (or two client sessions) load the same row and both attempt
    to save changes, the second commit raises
    :class:`sqlalchemy.orm.exc.StaleDataError` instead of silently
    overwriting the first change — critical for a multi-user, multi
    -threaded attendance system.
    """

    version: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, sort_order=96
    )

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:  # noqa: N805
        # Resolved against the already-assembled Table, not the class
        # dict, because Table construction from mixin columns completes
        # before __mapper_args__ is evaluated for the concrete subclass.
        return {"version_id_col": cls.__table__.c.version}


class SerializationMixin:
    """Adds generic, reflection-based (de)serialization helpers.

    These helpers introspect the SQLAlchemy mapper of the concrete model
    at call time, so every model gets working ``to_dict`` /
    ``update_from_dict`` / ``serialize`` without repeating field lists in
    each model file (DRY).
    """

    #: Columns that must never be mass-assigned through update_from_dict()
    #: without the caller explicitly whitelisting them via allowed_fields.
    _PROTECTED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "id",
            "public_id",
            "created_at",
            "updated_at",
            "created_by_id",
            "updated_by_id",
            "version",
            "is_deleted",
            "deleted_at",
        }
    )

    def _mapped_column_names(self) -> set[str]:
        """Return the set of attribute names backed by a mapped column."""
        return {attr.key for attr in sa_inspect(self).mapper.column_attrs}

    @staticmethod
    def _json_safe(value: Any) -> Any:
        """Convert a single Python value into a JSON-serializable form."""
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, uuid.UUID):
            return str(value)
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, Enum):
            return value.value
        return value

    def to_dict(self, *, exclude: Iterable[str] = ()) -> dict[str, Any]:
        """Serialize every mapped column of this instance into a ``dict``.

        Args:
            exclude: Column attribute names to omit from the result (for
                example ``{"created_by_id"}`` when the caller does not
                need audit metadata).

        Returns:
            A JSON-safe dictionary mapping column names to their current
            values.
        """
        excluded = set(exclude)
        return {
            name: self._json_safe(getattr(self, name))
            for name in self._mapped_column_names()
            if name not in excluded
        }

    def update_from_dict(
        self,
        data: Mapping[str, Any],
        *,
        allowed_fields: Iterable[str] | None = None,
    ) -> None:
        """Assign values from ``data`` onto this instance's mapped columns.

        By default, bookkeeping columns (:attr:`_PROTECTED_FIELDS` — the
        primary key, audit stamps, soft-delete flags, and the optimistic
        -locking version) are never touched, preventing accidental mass
        assignment from untrusted input such as a raw request payload.

        Args:
            data: Source values keyed by column attribute name. Keys that
                do not correspond to a mapped column, or that are not in
                the resolved allow-list, are silently ignored.
            allowed_fields: An explicit whitelist of column names that may
                be updated. When provided, it overrides the default
                protected-field filtering entirely — use this when a
                caller genuinely needs to update a normally protected
                column (for example, an administrative restore flow
                updating ``is_deleted``).
        """
        mapped_columns = self._mapped_column_names()
        if allowed_fields is not None:
            updatable_fields = set(allowed_fields) & mapped_columns
        else:
            updatable_fields = mapped_columns - self._PROTECTED_FIELDS

        for field_name in updatable_fields:
            if field_name in data:
                setattr(self, field_name, data[field_name])

    def serialize(self, *, exclude: Iterable[str] = ()) -> str:
        """Serialize this instance to a JSON string.

        Uses ``ensure_ascii=False`` so Arabic employee/company names are
        written as readable UTF-8 text rather than ``\\uXXXX`` escapes.

        Args:
            exclude: Column attribute names to omit, forwarded to
                :meth:`to_dict`.

        Returns:
            A UTF-8 JSON string representing this instance.
        """
        import json

        return json.dumps(self.to_dict(exclude=exclude), ensure_ascii=False)


class BaseModel(
    TableNameMixin,
    SerializationMixin,
    VersionMixin,
    AuditMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDMixin,
    IdMixin,
    Base,
):
    """Abstract base every concrete domain model must inherit from.

    Combines automatic table naming, an integer primary key, a public
    UUID, created/updated timestamps, soft delete, audit attribution and
    optimistic-locking version — plus reflection-based serialization
    helpers — so that a concrete model only has to declare the columns
    that are actually specific to it.

    Example:
        >>> class Department(BaseModel):
        ...     name: Mapped[str] = mapped_column(String(120))
    """

    __abstract__ = True

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return f"<{self.__class__.__name__} id={getattr(self, 'id', None)!r}>"

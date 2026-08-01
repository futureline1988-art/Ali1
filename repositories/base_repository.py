"""Generic repository base classes implementing the Repository Pattern.

:class:`BaseRepository` is the CRUD foundation every repository in this
package builds on. :class:`CompanyScopedRepository` is the actual
enforcement point for this application's multi-company data isolation:
every query method it defines adds a ``WHERE company_id = ...`` clause,
so a service that only ever talks to the database through a
``CompanyScopedRepository`` subclass cannot leak another tenant's rows
— not through carelessness, not through a copy-pasted query missing a
filter. See :class:`~models.base.CompanyScopedMixin` for the model-side
half of this contract.
"""

from __future__ import annotations

import uuid
from typing import Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.base import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


class BaseRepository(Generic[ModelT]):
    """Generic CRUD repository for a single ORM model.

    Every method operates against a caller-supplied
    :class:`~sqlalchemy.orm.Session` (see
    :func:`database.database.session_scope`) — repositories never open,
    commit, or close a session themselves. That is deliberate: a single
    service-layer operation often touches more than one repository
    inside one transaction (e.g. creating an
    :class:`~models.employee.Employee` and writing an
    :class:`~models.audit_log.AuditLog` entry), and only the service
    layer knows where that transaction actually begins and ends.

    Soft-deleted rows (:attr:`~models.base.SoftDeleteMixin.is_deleted`)
    are excluded from every query by default, per that mixin's
    documented contract.
    """

    def __init__(self, session: Session, *, model: type[ModelT]) -> None:
        """Create a repository bound to one session and one model.

        Args:
            session: The active session for this unit of work.
            model: The ORM model class this repository manages.
        """
        self.session = session
        self.model = model

    def get_by_id(
        self, entity_id: int, *, include_deleted: bool = False
    ) -> ModelT | None:
        """Fetch a single row by primary key.

        Args:
            entity_id: The row's ``id``.
            include_deleted: Whether to return the row even if it has
                been soft-deleted.

        Returns:
            The matching entity, or ``None`` if not found (or
            soft-deleted and ``include_deleted`` is ``False``).
        """
        entity = self.session.get(self.model, entity_id)
        if entity is None:
            return None
        if entity.is_deleted and not include_deleted:
            return None
        return entity

    def get_by_public_id(
        self, public_id: uuid.UUID, *, include_deleted: bool = False
    ) -> ModelT | None:
        """Fetch a single row by its externally-safe UUID.

        Args:
            public_id: The row's :attr:`~models.base.UUIDMixin.public_id`.
            include_deleted: Whether to include soft-deleted rows.

        Returns:
            The matching entity, or ``None`` if not found.
        """
        statement = select(self.model).where(self.model.public_id == public_id)
        if not include_deleted:
            statement = statement.where(self.model.is_deleted.is_(False))
        return self.session.execute(statement).scalar_one_or_none()

    def list_all(
        self,
        *,
        include_deleted: bool = False,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ModelT]:
        """List every row, ordered by primary key.

        Args:
            include_deleted: Whether to include soft-deleted rows.
            limit: Maximum number of rows to return.
            offset: Number of rows to skip (for pagination).

        Returns:
            The matching rows.
        """
        statement = select(self.model).order_by(self.model.id)
        if not include_deleted:
            statement = statement.where(self.model.is_deleted.is_(False))
        if offset is not None:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        return list(self.session.execute(statement).scalars().all())

    def count(self, *, include_deleted: bool = False) -> int:
        """Count rows.

        Args:
            include_deleted: Whether to include soft-deleted rows.

        Returns:
            The row count.
        """
        statement = select(func.count()).select_from(self.model)
        if not include_deleted:
            statement = statement.where(self.model.is_deleted.is_(False))
        return self.session.execute(statement).scalar_one()

    def add(self, entity: ModelT) -> ModelT:
        """Stage a new entity for insertion and flush it.

        Flushing (rather than only ``session.add``) assigns the
        entity's primary key immediately, so callers can use
        ``entity.id`` right away without waiting for the enclosing
        transaction to commit.

        Args:
            entity: The new, transient entity to persist.

        Returns:
            The same entity, now with its primary key assigned.
        """
        self.session.add(entity)
        self.session.flush()
        return entity

    def delete(self, entity: ModelT) -> None:
        """Soft-delete ``entity`` (see :meth:`~models.base.SoftDeleteMixin.soft_delete`)."""
        entity.soft_delete()
        self.session.flush()

    def restore(self, entity: ModelT) -> None:
        """Reverse a previous :meth:`delete` call on ``entity``."""
        entity.restore()
        self.session.flush()

    def hard_delete(self, entity: ModelT) -> None:
        """Permanently remove ``entity`` from the database.

        Rarely appropriate — prefer :meth:`delete` (soft delete) for
        anything with audit or reporting value. Reserved for cleanup of
        genuinely disposable data.

        Args:
            entity: The entity to permanently remove.
        """
        self.session.delete(entity)
        self.session.flush()


class CompanyScopedRepository(BaseRepository[ModelT], Generic[ModelT]):
    """A repository whose every query is confined to one company.

    Bound to a single ``company_id`` at construction time (matching how
    a desktop session naturally has exactly one "current company" for
    its duration — see
    :attr:`~utils.security.SessionManager.current_company_id`), so every
    method below can safely add a ``company_id`` filter without the
    caller having to remember to pass it on every call.
    """

    def __init__(
        self, session: Session, *, model: type[ModelT], company_id: int
    ) -> None:
        """Create a repository bound to one session, model and company.

        Args:
            session: The active session for this unit of work.
            model: The company-scoped ORM model class this repository
                manages (must inherit
                :class:`~models.base.CompanyScopedMixin`).
            company_id: The company every query is confined to.
        """
        super().__init__(session, model=model)
        self.company_id = company_id

    def get_by_id(
        self, entity_id: int, *, include_deleted: bool = False
    ) -> ModelT | None:
        """Fetch a row by primary key, scoped to this repository's company."""
        entity = super().get_by_id(entity_id, include_deleted=include_deleted)
        if entity is not None and entity.company_id != self.company_id:
            return None
        return entity

    def get_by_public_id(
        self, public_id: uuid.UUID, *, include_deleted: bool = False
    ) -> ModelT | None:
        """Fetch a row by public UUID, scoped to this repository's company."""
        statement = select(self.model).where(
            self.model.public_id == public_id,
            self.model.company_id == self.company_id,
        )
        if not include_deleted:
            statement = statement.where(self.model.is_deleted.is_(False))
        return self.session.execute(statement).scalar_one_or_none()

    def list_all(
        self,
        *,
        include_deleted: bool = False,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ModelT]:
        """List every row for this repository's company, ordered by primary key."""
        statement = (
            select(self.model)
            .where(self.model.company_id == self.company_id)
            .order_by(self.model.id)
        )
        if not include_deleted:
            statement = statement.where(self.model.is_deleted.is_(False))
        if offset is not None:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        return list(self.session.execute(statement).scalars().all())

    def count(self, *, include_deleted: bool = False) -> int:
        """Count rows belonging to this repository's company."""
        statement = (
            select(func.count())
            .select_from(self.model)
            .where(self.model.company_id == self.company_id)
        )
        if not include_deleted:
            statement = statement.where(self.model.is_deleted.is_(False))
        return self.session.execute(statement).scalar_one()

    def add(self, entity: ModelT) -> ModelT:
        """Stage a new entity for insertion, refusing a company mismatch.

        Args:
            entity: The new, transient entity to persist. Its
                ``company_id`` must already equal this repository's
                ``company_id``.

        Raises:
            ValueError: If ``entity.company_id`` does not match this
                repository's ``company_id`` — a service-layer bug that
                would otherwise silently create cross-tenant data.
        """
        if entity.company_id != self.company_id:
            raise ValueError(
                f"Refusing to add an entity with company_id={entity.company_id!r} "
                f"through a repository scoped to company_id={self.company_id!r}."
            )
        return super().add(entity)

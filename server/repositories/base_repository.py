"""Generic CRUD repository base for Attendance Server models.

See this package's ``__init__.py`` for why this is a small,
independent class rather than a reuse of
``repositories.base_repository.BaseRepository`` or
``developer_suite.repositories.base_repository.BaseRepository`` — same
pattern, a third, unrelated model hierarchy.
"""

from __future__ import annotations

import uuid
from typing import Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.database.base import ServerBaseModel

ModelT = TypeVar("ModelT", bound=ServerBaseModel)


class BaseRepository(Generic[ModelT]):
    """Generic CRUD repository for a single Attendance Server ORM model.

    Every method operates against a caller-supplied
    :class:`~sqlalchemy.orm.Session` — repositories never open, commit,
    or close a session themselves; that is the service layer's job
    (see :mod:`server.services.base_service`).

    Soft-deleted rows are excluded from every query by default.
    """

    def __init__(self, session: Session, *, model: type[ModelT]) -> None:
        """Create a repository bound to one session and one model.

        Args:
            session: The active session for this unit of work.
            model: The ORM model class this repository manages.
        """
        self.session = session
        self.model = model

    def get_by_id(self, entity_id: int, *, include_deleted: bool = False) -> ModelT | None:
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
            public_id: The row's ``public_id``.
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

        Args:
            entity: The new, transient entity to persist.

        Returns:
            The same entity, now with its primary key assigned.
        """
        self.session.add(entity)
        self.session.flush()
        return entity

    def delete(self, entity: ModelT) -> None:
        """Soft-delete ``entity``."""
        entity.soft_delete()
        self.session.flush()

    def restore(self, entity: ModelT) -> None:
        """Reverse a previous :meth:`delete` call on ``entity``."""
        entity.restore()
        self.session.flush()

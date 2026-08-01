"""Repository for :class:`~models.permission.Permission` — the global capability catalog.

Not company-scoped — every tenant draws from the same fixed catalog —
so this extends :class:`~repositories.base_repository.BaseRepository`
directly.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.permission import Permission
from repositories.base_repository import BaseRepository


class PermissionRepository(BaseRepository[Permission]):
    """Data access for :class:`~models.permission.Permission`."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        super().__init__(session, model=Permission)

    def get_by_code(self, code: str) -> Permission | None:
        """Fetch a permission by its unique machine code.

        Args:
            code: The permission's
                :attr:`~models.permission.Permission.code`
                (e.g. ``"employees.create"``).

        Returns:
            The matching permission, or ``None``.
        """
        statement = select(Permission).where(Permission.code == code)
        return self.session.execute(statement).scalar_one_or_none()

    def list_by_module(self, module: str) -> list[Permission]:
        """List every permission belonging to one module.

        Args:
            module: The :attr:`~models.permission.Permission.module`
                grouping key (e.g. ``"employees"``).

        Returns:
            Matching permissions, ordered by code.
        """
        statement = (
            select(Permission)
            .where(Permission.module == module)
            .order_by(Permission.code)
        )
        return list(self.session.execute(statement).scalars().all())

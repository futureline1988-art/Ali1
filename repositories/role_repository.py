"""Repository for :class:`~models.role.Role`."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.role import Role
from repositories.base_repository import CompanyScopedRepository


class RoleRepository(CompanyScopedRepository[Role]):
    """Data access for :class:`~models.role.Role`, scoped to one company."""

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create a repository bound to ``session`` and ``company_id``."""
        super().__init__(session, model=Role, company_id=company_id)

    def get_by_code(self, code: str) -> Role | None:
        """Fetch this company's role matching a built-in role code.

        Args:
            code: A :class:`~models.enums.UserRole` value (e.g.
                ``"hr"``) used as the seeded role's
                :attr:`~models.role.Role.code`.

        Returns:
            The matching role, or ``None`` if this company has not been
            seeded with it (or it was renamed away from a code).
        """
        statement = select(Role).where(
            Role.company_id == self.company_id,
            Role.code == code,
            Role.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

    def get_by_name(self, name: str) -> Role | None:
        """Fetch this company's role by its unique-per-company display name.

        Args:
            name: The role's :attr:`~models.role.Role.name`.

        Returns:
            The matching role, or ``None``.
        """
        statement = select(Role).where(
            Role.company_id == self.company_id,
            Role.name == name,
            Role.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

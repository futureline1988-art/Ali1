"""Repository for :class:`~models.department.Department`."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.department import Department
from repositories.base_repository import CompanyScopedRepository


class DepartmentRepository(CompanyScopedRepository[Department]):
    """Data access for :class:`~models.department.Department`, scoped to one company."""

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create a repository bound to ``session`` and ``company_id``."""
        super().__init__(session, model=Department, company_id=company_id)

    def get_by_code(self, code: str) -> Department | None:
        """Fetch a department by its unique-per-company short code.

        Args:
            code: The department's
                :attr:`~models.department.Department.code`.

        Returns:
            The matching department, or ``None``.
        """
        statement = select(Department).where(
            Department.company_id == self.company_id,
            Department.code == code,
            Department.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

    def get_by_name(self, name: str) -> Department | None:
        """Fetch a department by its unique-per-company name.

        Args:
            name: The department's
                :attr:`~models.department.Department.name`.

        Returns:
            The matching department, or ``None``.
        """
        statement = select(Department).where(
            Department.company_id == self.company_id,
            Department.name == name,
            Department.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

    def list_root_departments(self) -> list[Department]:
        """List this company's top-level departments (no parent).

        Returns:
            Departments with no
            :attr:`~models.department.Department.parent_department_id`,
            ordered by name — a natural starting point for rendering
            the department hierarchy tree.
        """
        statement = (
            select(Department)
            .where(
                Department.company_id == self.company_id,
                Department.parent_department_id.is_(None),
                Department.is_deleted.is_(False),
            )
            .order_by(Department.name)
        )
        return list(self.session.execute(statement).scalars().all())

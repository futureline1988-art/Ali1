"""Repository for :class:`~models.employee.Employee`."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.employee import Employee
from models.enums import EmploymentStatus
from repositories.base_repository import CompanyScopedRepository


class EmployeeRepository(CompanyScopedRepository[Employee]):
    """Data access for :class:`~models.employee.Employee`, scoped to one company."""

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create a repository bound to ``session`` and ``company_id``."""
        super().__init__(session, model=Employee, company_id=company_id)

    def get_by_employee_number(self, employee_number: str) -> Employee | None:
        """Fetch an employee by their unique-per-company business number.

        Args:
            employee_number: The employee's
                :attr:`~models.employee.Employee.employee_number`.

        Returns:
            The matching employee, or ``None``.
        """
        statement = select(Employee).where(
            Employee.company_id == self.company_id,
            Employee.employee_number == employee_number,
            Employee.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

    def get_by_national_id(self, national_id: str) -> Employee | None:
        """Fetch an employee by their unique-per-company national ID.

        Args:
            national_id: The employee's
                :attr:`~models.employee.Employee.national_id`.

        Returns:
            The matching employee, or ``None``.
        """
        statement = select(Employee).where(
            Employee.company_id == self.company_id,
            Employee.national_id == national_id,
            Employee.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

    def list_by_department(self, department_id: int) -> list[Employee]:
        """List every employee assigned to one department.

        Args:
            department_id: The department's id.

        Returns:
            Matching employees, ordered by full name.
        """
        statement = (
            select(Employee)
            .where(
                Employee.company_id == self.company_id,
                Employee.department_id == department_id,
                Employee.is_deleted.is_(False),
            )
            .order_by(Employee.full_name)
        )
        return list(self.session.execute(statement).scalars().all())

    def list_active(self) -> list[Employee]:
        """List every currently-active employee in this company.

        Returns:
            Employees with
            :attr:`~models.employee.Employee.employment_status` set to
            :attr:`~models.enums.EmploymentStatus.ACTIVE`, ordered by
            full name.
        """
        statement = (
            select(Employee)
            .where(
                Employee.company_id == self.company_id,
                Employee.employment_status == EmploymentStatus.ACTIVE,
                Employee.is_deleted.is_(False),
            )
            .order_by(Employee.full_name)
        )
        return list(self.session.execute(statement).scalars().all())

    def search_by_name(self, query: str) -> list[Employee]:
        """Search employees whose name contains ``query`` (case-insensitive).

        Args:
            query: A partial name to search for.

        Returns:
            Matching employees, ordered by full name.
        """
        statement = (
            select(Employee)
            .where(
                Employee.company_id == self.company_id,
                Employee.full_name.ilike(f"%{query}%"),
                Employee.is_deleted.is_(False),
            )
            .order_by(Employee.full_name)
        )
        return list(self.session.execute(statement).scalars().all())

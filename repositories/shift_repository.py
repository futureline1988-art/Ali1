"""Repositories for :class:`~models.shift.Shift` and
:class:`~models.shift.EmployeeShiftAssignment`.

Grouped in one file, mirroring how the two models are grouped in
``models/shift.py``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.shift import EmployeeShiftAssignment, Shift
from repositories.base_repository import CompanyScopedRepository


class ShiftRepository(CompanyScopedRepository[Shift]):
    """Data access for :class:`~models.shift.Shift`, scoped to one company."""

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create a repository bound to ``session`` and ``company_id``."""
        super().__init__(session, model=Shift, company_id=company_id)

    def get_by_name(self, name: str) -> Shift | None:
        """Fetch a shift by its unique-per-company name.

        Args:
            name: The shift's :attr:`~models.shift.Shift.name`.

        Returns:
            The matching shift, or ``None``.
        """
        statement = select(Shift).where(
            Shift.company_id == self.company_id,
            Shift.name == name,
            Shift.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

    def list_active(self) -> list[Shift]:
        """List every shift currently assignable in this company.

        Returns:
            Shifts with :attr:`~models.shift.Shift.is_active` set,
            ordered by name.
        """
        statement = (
            select(Shift)
            .where(
                Shift.company_id == self.company_id,
                Shift.is_active.is_(True),
                Shift.is_deleted.is_(False),
            )
            .order_by(Shift.name)
        )
        return list(self.session.execute(statement).scalars().all())


class EmployeeShiftAssignmentRepository(CompanyScopedRepository[EmployeeShiftAssignment]):
    """Data access for :class:`~models.shift.EmployeeShiftAssignment`."""

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create a repository bound to ``session`` and ``company_id``."""
        super().__init__(session, model=EmployeeShiftAssignment, company_id=company_id)

    def get_current_for_employee(
        self, employee_id: int
    ) -> EmployeeShiftAssignment | None:
        """Fetch an employee's current, open-ended shift assignment.

        Args:
            employee_id: The employee's id.

        Returns:
            The assignment with
            :attr:`~models.shift.EmployeeShiftAssignment.effective_to`
            unset, or ``None`` if the employee has no current
            assignment.
        """
        statement = select(EmployeeShiftAssignment).where(
            EmployeeShiftAssignment.company_id == self.company_id,
            EmployeeShiftAssignment.employee_id == employee_id,
            EmployeeShiftAssignment.effective_to.is_(None),
            EmployeeShiftAssignment.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

    def list_for_employee(self, employee_id: int) -> list[EmployeeShiftAssignment]:
        """List an employee's full shift-assignment history.

        Args:
            employee_id: The employee's id.

        Returns:
            Assignments ordered from most to least recent
            (``effective_from`` descending).
        """
        statement = (
            select(EmployeeShiftAssignment)
            .where(
                EmployeeShiftAssignment.company_id == self.company_id,
                EmployeeShiftAssignment.employee_id == employee_id,
                EmployeeShiftAssignment.is_deleted.is_(False),
            )
            .order_by(EmployeeShiftAssignment.effective_from.desc())
        )
        return list(self.session.execute(statement).scalars().all())

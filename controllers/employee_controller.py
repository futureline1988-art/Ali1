"""Employee controller: bridges the employees screen to ``EmployeeService``."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from PySide6.QtCore import Signal
from sqlalchemy.orm import Session

from controllers.base_controller import BaseController
from models.employee import Employee
from repositories.employee_repository import EmployeeRepository
from services.employee_service import EmployeeService


def _employee_to_dict(employee: Employee) -> dict[str, Any]:
    """Serialize an employee plus its department name, while the session is open."""
    data = employee.to_dict()
    data["department_name"] = employee.department.name if employee.department else None
    data["employment_status_label_ar"] = employee.employment_status_label_ar
    data["employment_status_label_en"] = employee.employment_status_label_en
    return data


class EmployeeController(BaseController):
    """Controller for the employees management screen."""

    employees_changed = Signal()
    """Emitted after any successful create/update/delete, so the UI can refresh."""

    def create_employee(
        self,
        *,
        employee_number: str,
        full_name: str,
        department_id: int | None = None,
        branch_id: int | None = None,
        national_id: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        position: str | None = None,
        salary: Decimal | None = None,
        hire_date: date | None = None,
        notes: str | None = None,
    ) -> dict[str, Any] | None:
        """Create a new employee.

        Args mirror :meth:`~services.employee_service.EmployeeService.create_employee`.

        Returns:
            The new employee's data as a dict, or ``None`` on failure.
        """

        def do_create(session: Session) -> dict[str, Any]:
            service = EmployeeService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            employee = service.create_employee(
                employee_number=employee_number,
                full_name=full_name,
                department_id=department_id,
                branch_id=branch_id,
                national_id=national_id,
                email=email,
                phone=phone,
                position=position,
                salary=salary,
                hire_date=hire_date,
                notes=notes,
            )
            return _employee_to_dict(employee)

        result = self._run(do_create)
        if result is not None:
            self.employees_changed.emit()
        return result

    def update_employee(self, employee_id: int, **fields: Any) -> dict[str, Any] | None:
        """Update an existing employee.

        Args:
            employee_id: The employee to update.
            **fields: Fields to change; see
                :meth:`~services.employee_service.EmployeeService.update_employee`.

        Returns:
            The updated employee's data as a dict, or ``None`` on
            failure.
        """

        def do_update(session: Session) -> dict[str, Any]:
            repo = EmployeeRepository(session, company_id=self.company_id)
            employee = repo.get_by_id(employee_id)
            if employee is None:
                raise ValueError(f"Employee {employee_id!r} was not found.")
            service = EmployeeService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            updated = service.update_employee(employee, **fields)
            return _employee_to_dict(updated)

        result = self._run(do_update)
        if result is not None:
            self.employees_changed.emit()
        return result

    def delete_employee(self, employee_id: int) -> bool:
        """Soft-delete an employee.

        Args:
            employee_id: The employee to delete.

        Returns:
            ``True`` on success, ``False`` on failure.
        """

        def do_delete(session: Session) -> bool:
            repo = EmployeeRepository(session, company_id=self.company_id)
            employee = repo.get_by_id(employee_id)
            if employee is None:
                raise ValueError(f"Employee {employee_id!r} was not found.")
            service = EmployeeService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            service.delete_employee(employee)
            return True

        result = self._run(do_delete)
        if result:
            self.employees_changed.emit()
        return bool(result)

    def get_employee(self, employee_id: int) -> dict[str, Any] | None:
        """Fetch one employee.

        Args:
            employee_id: The employee to fetch.

        Returns:
            The employee's data as a dict, or ``None`` if not found or
            on failure.
        """

        def do_get(session: Session) -> dict[str, Any] | None:
            repo = EmployeeRepository(session, company_id=self.company_id)
            employee = repo.get_by_id(employee_id)
            return _employee_to_dict(employee) if employee is not None else None

        return self._run(do_get)

    def list_employees(
        self, *, department_id: int | None = None, active_only: bool = False
    ) -> list[dict[str, Any]]:
        """List employees, optionally filtered.

        Args:
            department_id: Restrict to one department, if given.
            active_only: Restrict to active employees.

        Returns:
            Matching employees' data as dicts; an empty list on
            failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            service = EmployeeService(session, company_id=self.company_id)
            employees = service.list_employees(
                department_id=department_id, active_only=active_only
            )
            return [_employee_to_dict(employee) for employee in employees]

        return self._run(do_list) or []

    def search_employees(self, query: str) -> list[dict[str, Any]]:
        """Search employees by partial name.

        Args:
            query: Partial name to search for.

        Returns:
            Matching employees' data as dicts; an empty list on
            failure.
        """

        def do_search(session: Session) -> list[dict[str, Any]]:
            repo = EmployeeRepository(session, company_id=self.company_id)
            return [_employee_to_dict(employee) for employee in repo.search_by_name(query)]

        return self._run(do_search) or []

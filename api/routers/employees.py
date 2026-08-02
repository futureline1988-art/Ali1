"""Employee CRUD endpoints — the HTTP mirror of ``controllers/employee_controller.py``.

Every route resolves the target employee through
:class:`~repositories.employee_repository.EmployeeRepository` scoped
to the caller's own ``company_id`` (from their bearer token), so one
tenant can never read or modify another tenant's employees no matter
what id is requested — the same multi-tenant boundary every other
company-scoped repository in this project enforces.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from api.dependencies import CurrentUser, get_db_session, require_permission
from api.schemas import EmployeeCreateRequest, EmployeeUpdateRequest
from models.employee import Employee
from repositories.employee_repository import EmployeeRepository
from services.employee_service import EmployeeService, EmployeeValidationError

router = APIRouter(prefix="/api/employees", tags=["employees"])


def _employee_to_dict(employee: Employee) -> dict[str, Any]:
    """Serialize an employee plus its department name, while the session is open."""
    data = employee.to_dict()
    data["department_name"] = employee.department.name if employee.department else None
    data["employment_status_label_ar"] = employee.employment_status_label_ar
    data["employment_status_label_en"] = employee.employment_status_label_en
    return data


def _get_employee_or_404(session: Session, *, company_id: int, employee_id: int) -> Employee:
    """Fetch an employee scoped to ``company_id``, or raise a 404."""
    employee = EmployeeRepository(session, company_id=company_id).get_by_id(employee_id)
    if employee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found.")
    return employee


@router.get("")
def list_employees(
    department_id: int | None = None,
    active_only: bool = False,
    current_user: CurrentUser = Depends(require_permission("employees.view", "employees.manage")),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """List employees in the caller's company, optionally filtered."""
    service = EmployeeService(session, company_id=current_user.company_id)
    employees = service.list_employees(department_id=department_id, active_only=active_only)
    return [_employee_to_dict(employee) for employee in employees]


@router.get("/{employee_id}")
def get_employee(
    employee_id: int,
    current_user: CurrentUser = Depends(require_permission("employees.view", "employees.manage")),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Fetch a single employee by id."""
    employee = _get_employee_or_404(
        session, company_id=current_user.company_id, employee_id=employee_id
    )
    return _employee_to_dict(employee)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_employee(
    payload: EmployeeCreateRequest,
    current_user: CurrentUser = Depends(require_permission("employees.manage")),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Create a new employee in the caller's company."""
    service = EmployeeService(
        session, company_id=current_user.company_id, actor_user_id=current_user.user_id
    )
    try:
        employee = service.create_employee(**payload.model_dump())
    except EmployeeValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _employee_to_dict(employee)


@router.put("/{employee_id}")
def update_employee(
    employee_id: int,
    payload: EmployeeUpdateRequest,
    current_user: CurrentUser = Depends(require_permission("employees.manage")),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Update an existing employee's editable fields (partial update)."""
    employee = _get_employee_or_404(
        session, company_id=current_user.company_id, employee_id=employee_id
    )
    service = EmployeeService(
        session, company_id=current_user.company_id, actor_user_id=current_user.user_id
    )
    fields = payload.model_dump(exclude_unset=True)
    try:
        employee = service.update_employee(employee, **fields)
    except EmployeeValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _employee_to_dict(employee)


@router.delete("/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_employee(
    employee_id: int,
    current_user: CurrentUser = Depends(require_permission("employees.manage")),
    session: Session = Depends(get_db_session),
) -> Response:
    """Soft-delete an employee."""
    employee = _get_employee_or_404(
        session, company_id=current_user.company_id, employee_id=employee_id
    )
    service = EmployeeService(
        session, company_id=current_user.company_id, actor_user_id=current_user.user_id
    )
    service.delete_employee(employee)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

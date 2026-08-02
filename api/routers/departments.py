"""Department listing/CRUD endpoints — the HTTP mirror of ``controllers/department_controller.py``."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from api.dependencies import CurrentUser, get_db_session, require_permission
from api.schemas import DepartmentCreateRequest
from models.department import Department
from repositories.department_repository import DepartmentRepository
from services.department_service import DepartmentService, DepartmentValidationError

router = APIRouter(prefix="/api/departments", tags=["departments"])


def _department_to_dict(department: Department) -> dict[str, Any]:
    """Serialize a department plus its parent's name, while the session is open."""
    data = department.to_dict()
    data["parent_name"] = department.parent.name if department.parent else None
    return data


def _get_department_or_404(
    session: Session, *, company_id: int, department_id: int
) -> Department:
    """Fetch a department scoped to ``company_id``, or raise a 404."""
    department = DepartmentRepository(session, company_id=company_id).get_by_id(department_id)
    if department is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Department not found."
        )
    return department


@router.get("")
def list_departments(
    current_user: CurrentUser = Depends(
        require_permission("departments.view", "departments.manage")
    ),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """List every department in the caller's company."""
    departments = DepartmentRepository(session, company_id=current_user.company_id).list_all()
    return [_department_to_dict(department) for department in departments]


@router.get("/{department_id}")
def get_department(
    department_id: int,
    current_user: CurrentUser = Depends(
        require_permission("departments.view", "departments.manage")
    ),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Fetch a single department by id."""
    department = _get_department_or_404(
        session, company_id=current_user.company_id, department_id=department_id
    )
    return _department_to_dict(department)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_department(
    payload: DepartmentCreateRequest,
    current_user: CurrentUser = Depends(require_permission("departments.manage")),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Create a new department in the caller's company."""
    service = DepartmentService(
        session, company_id=current_user.company_id, actor_user_id=current_user.user_id
    )
    try:
        department = service.create_department(**payload.model_dump())
    except DepartmentValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return _department_to_dict(department)


@router.delete("/{department_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_department(
    department_id: int,
    current_user: CurrentUser = Depends(require_permission("departments.manage")),
    session: Session = Depends(get_db_session),
) -> Response:
    """Soft-delete a department."""
    department = _get_department_or_404(
        session, company_id=current_user.company_id, department_id=department_id
    )
    service = DepartmentService(
        session, company_id=current_user.company_id, actor_user_id=current_user.user_id
    )
    service.delete_department(department)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

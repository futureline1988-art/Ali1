"""Department controller: bridges the departments screen to ``DepartmentService``."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from sqlalchemy.orm import Session

from controllers.base_controller import BaseController
from models.department import Department
from repositories.department_repository import DepartmentRepository
from services.department_service import DepartmentService


def _department_to_dict(department: Department) -> dict[str, Any]:
    """Serialize a department plus its breadcrumb path, while the session is open."""
    data = department.to_dict()
    data["full_path"] = department.full_path
    data["children_count"] = len(department.children)
    return data


class DepartmentController(BaseController):
    """Controller for the departments management screen."""

    departments_changed = Signal()
    """Emitted after any successful create/update/move/delete."""

    def create_department(
        self,
        *,
        name: str,
        code: str | None = None,
        description: str | None = None,
        parent_department_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Create a new department.

        Args mirror :meth:`~services.department_service.DepartmentService.create_department`.

        Returns:
            The new department's data as a dict, or ``None`` on failure.
        """

        def do_create(session: Session) -> dict[str, Any]:
            service = DepartmentService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            department = service.create_department(
                name=name,
                code=code,
                description=description,
                parent_department_id=parent_department_id,
            )
            return _department_to_dict(department)

        result = self._run(do_create)
        if result is not None:
            self.departments_changed.emit()
        return result

    def update_department(self, department_id: int, **fields: Any) -> dict[str, Any] | None:
        """Update a department's editable fields (not its parent).

        Args:
            department_id: The department to update.
            **fields: See
                :meth:`~services.department_service.DepartmentService.update_department`.

        Returns:
            The updated department's data as a dict, or ``None`` on
            failure.
        """

        def do_update(session: Session) -> dict[str, Any]:
            repo = DepartmentRepository(session, company_id=self.company_id)
            department = repo.get_by_id(department_id)
            if department is None:
                raise ValueError(f"Department {department_id!r} was not found.")
            service = DepartmentService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            updated = service.update_department(department, **fields)
            return _department_to_dict(updated)

        result = self._run(do_update)
        if result is not None:
            self.departments_changed.emit()
        return result

    def move_department(
        self, department_id: int, *, new_parent_id: int | None
    ) -> dict[str, Any] | None:
        """Re-parent a department.

        Args:
            department_id: The department to move.
            new_parent_id: The new parent, or ``None`` for root.

        Returns:
            The updated department's data as a dict, or ``None`` on
            failure (e.g. a cycle was rejected).
        """

        def do_move(session: Session) -> dict[str, Any]:
            repo = DepartmentRepository(session, company_id=self.company_id)
            department = repo.get_by_id(department_id)
            if department is None:
                raise ValueError(f"Department {department_id!r} was not found.")
            service = DepartmentService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            updated = service.move_department(department, new_parent_id=new_parent_id)
            return _department_to_dict(updated)

        result = self._run(do_move)
        if result is not None:
            self.departments_changed.emit()
        return result

    def delete_department(self, department_id: int) -> bool:
        """Soft-delete a department.

        Args:
            department_id: The department to delete.

        Returns:
            ``True`` on success, ``False`` on failure.
        """

        def do_delete(session: Session) -> bool:
            repo = DepartmentRepository(session, company_id=self.company_id)
            department = repo.get_by_id(department_id)
            if department is None:
                raise ValueError(f"Department {department_id!r} was not found.")
            service = DepartmentService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            service.delete_department(department)
            return True

        result = self._run(do_delete)
        if result:
            self.departments_changed.emit()
        return bool(result)

    def list_hierarchy_roots(self) -> list[dict[str, Any]]:
        """List this company's top-level departments.

        Returns:
            Root departments' data as dicts; an empty list on failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            service = DepartmentService(session, company_id=self.company_id)
            return [_department_to_dict(dept) for dept in service.list_hierarchy_roots()]

        return self._run(do_list) or []

    def list_all(self) -> list[dict[str, Any]]:
        """List every department in this company (flat, not just roots).

        Returns:
            All departments' data as dicts; an empty list on failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            repo = DepartmentRepository(session, company_id=self.company_id)
            return [_department_to_dict(dept) for dept in repo.list_all()]

        return self._run(do_list) or []

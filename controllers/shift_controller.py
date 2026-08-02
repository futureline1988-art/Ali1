"""Shift controller: bridges the shifts screen to ``ShiftService``."""

from __future__ import annotations

from datetime import date
from typing import Any

from PySide6.QtCore import Signal
from sqlalchemy.orm import Session

from controllers.base_controller import BaseController, requires_permission
from models.shift import EmployeeShiftAssignment, Shift
from repositories.employee_repository import EmployeeRepository
from repositories.shift_repository import ShiftRepository
from services.shift_service import ShiftService


def _shift_to_dict(shift: Shift) -> dict[str, Any]:
    """Serialize a shift, formatting its clock-time columns as ``HH:MM`` strings."""
    data = shift.to_dict()
    data["start_time"] = shift.start_time.strftime("%H:%M")
    data["end_time"] = shift.end_time.strftime("%H:%M")
    data["spans_midnight"] = shift.spans_midnight
    data["duration_minutes"] = shift.duration_minutes
    return data


def _assignment_to_dict(assignment: EmployeeShiftAssignment) -> dict[str, Any]:
    """Serialize an assignment plus the employee/shift names, while the session is open."""
    data = assignment.to_dict()
    data["employee_name"] = assignment.employee.full_name
    data["shift_name"] = assignment.shift.name
    data["is_current"] = assignment.is_current
    return data


class ShiftController(BaseController):
    """Controller for the shifts management screen."""

    shifts_changed = Signal()
    """Emitted after any successful create/update/delete of a shift."""

    assignments_changed = Signal()
    """Emitted after a successful employee shift assignment."""

    @requires_permission("shifts.manage")
    def create_shift(self, **fields: Any) -> dict[str, Any] | None:
        """Create a new shift.

        Args mirror :meth:`~services.shift_service.ShiftService.create_shift`.

        Returns:
            The new shift's data as a dict, or ``None`` on failure.
        """

        def do_create(session: Session) -> dict[str, Any]:
            service = ShiftService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            shift = service.create_shift(**fields)
            return _shift_to_dict(shift)

        result = self._run(do_create)
        if result is not None:
            self.shifts_changed.emit()
        return result

    @requires_permission("shifts.manage")
    def update_shift(self, shift_id: int, **fields: Any) -> dict[str, Any] | None:
        """Update a shift's editable fields.

        Args:
            shift_id: The shift to update.
            **fields: See
                :meth:`~services.shift_service.ShiftService.update_shift`.

        Returns:
            The updated shift's data as a dict, or ``None`` on failure.
        """

        def do_update(session: Session) -> dict[str, Any]:
            repo = ShiftRepository(session, company_id=self.company_id)
            shift = repo.get_by_id(shift_id)
            if shift is None:
                raise ValueError(f"Shift {shift_id!r} was not found.")
            service = ShiftService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            updated = service.update_shift(shift, **fields)
            return _shift_to_dict(updated)

        result = self._run(do_update)
        if result is not None:
            self.shifts_changed.emit()
        return result

    @requires_permission("shifts.manage", default=False)
    def delete_shift(self, shift_id: int) -> bool:
        """Soft-delete a shift.

        Args:
            shift_id: The shift to delete.

        Returns:
            ``True`` on success, ``False`` on failure.
        """

        def do_delete(session: Session) -> bool:
            repo = ShiftRepository(session, company_id=self.company_id)
            shift = repo.get_by_id(shift_id)
            if shift is None:
                raise ValueError(f"Shift {shift_id!r} was not found.")
            service = ShiftService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            service.delete_shift(shift)
            return True

        result = self._run(do_delete)
        if result:
            self.shifts_changed.emit()
        return bool(result)

    @requires_permission("shifts.view", "shifts.manage", default=[])
    def list_shifts(self) -> list[dict[str, Any]]:
        """List every shift defined for this company.

        Returns:
            Shifts' data as dicts; an empty list on failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            service = ShiftService(session, company_id=self.company_id)
            return [_shift_to_dict(shift) for shift in service.list_all()]

        return self._run(do_list) or []

    @requires_permission("shifts.manage")
    def assign_employee(
        self,
        *,
        employee_id: int,
        shift_id: int,
        effective_from: date,
        notes: str | None = None,
    ) -> dict[str, Any] | None:
        """Assign an employee to a shift.

        Args mirror :meth:`~services.shift_service.ShiftService.assign_employee`.

        Returns:
            The new assignment's data as a dict, or ``None`` on failure.
        """

        def do_assign(session: Session) -> dict[str, Any]:
            service = ShiftService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            assignment = service.assign_employee(
                employee_id=employee_id,
                shift_id=shift_id,
                effective_from=effective_from,
                notes=notes,
            )
            return _assignment_to_dict(assignment)

        result = self._run(do_assign)
        if result is not None:
            self.assignments_changed.emit()
        return result

    @requires_permission("shifts.view", "shifts.manage", default=[])
    def list_assignment_history(self, employee_id: int) -> list[dict[str, Any]]:
        """List an employee's full shift-assignment history.

        Args:
            employee_id: The employee's id.

        Returns:
            Assignments' data as dicts, most recent first; an empty
            list on failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            service = ShiftService(session, company_id=self.company_id)
            return [
                _assignment_to_dict(assignment)
                for assignment in service.list_assignment_history(employee_id)
            ]

        return self._run(do_list) or []

    @requires_permission("shifts.manage", default=[])
    def list_employees_for_assignment(self) -> list[dict[str, Any]]:
        """List every active employee, for the assignment picker.

        Returns:
            Employees' data as dicts; an empty list on failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            repo = EmployeeRepository(session, company_id=self.company_id)
            return [
                employee.to_dict()
                for employee in repo.list_all()
                if employee.is_currently_active
            ]

        return self._run(do_list) or []

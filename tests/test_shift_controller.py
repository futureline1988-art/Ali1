"""ShiftController: permission gating and explicit (never implicit) shift assignment.

Exercises the real controller (not the service directly) so this
proves the same UI -> controller -> service -> repository chain the
shifts screen actually uses -- including the product requirement that
a shift is only ever applied to an employee through an explicit
``assign_employee`` call, never automatically to "everyone."
"""

from __future__ import annotations

from datetime import date, time

import pytest

from controllers.shift_controller import ShiftController
from database.database import session_scope
from services.employee_service import EmployeeService


def _controller(company_id: int, *, codes: frozenset[str] = frozenset()) -> ShiftController:
    return ShiftController(company_id=company_id, actor_user_id=None, permission_codes=codes)


class TestPermissionGating:
    def test_create_shift_denied_without_permission(self, qapp, company_factory):
        company_id = company_factory()
        controller = _controller(company_id)
        denials = []
        controller.operation_failed.connect(denials.append)

        result = controller.create_shift(
            name="وردية صباحية", start_time=time(8, 0), end_time=time(16, 0)
        )
        assert result is None
        assert len(denials) == 1
        assert "صلاحية" in denials[0]

    def test_list_shifts_denied_returns_empty_list_not_none(self, qapp, company_factory):
        company_id = company_factory()
        controller = _controller(company_id)
        assert controller.list_shifts() == []

    def test_assign_employee_denied_without_manage_permission(self, qapp, company_factory):
        company_id = company_factory()
        controller = _controller(company_id, codes=frozenset({"shifts.view"}))
        result = controller.assign_employee(
            employee_id=1, shift_id=1, effective_from=date(2026, 1, 1)
        )
        assert result is None


class TestExplicitAssignmentThroughController:
    def test_shift_is_only_applied_via_explicit_per_employee_assignment(
        self, qapp, company_factory
    ):
        """No employee has a shift until assign_employee is explicitly called for them --
        creating a shift alone must never implicitly apply it to anyone.
        """
        company_id = company_factory()
        with session_scope() as session:
            employee_service = EmployeeService(session, company_id=company_id)
            assigned_employee = employee_service.create_employee(
                employee_number="B-001", full_name="عمر خالد"
            )
            unassigned_employee = employee_service.create_employee(
                employee_number="B-002", full_name="ريم عادل"
            )
            assigned_id, unassigned_id = assigned_employee.id, unassigned_employee.id

        controller = _controller(company_id, codes=frozenset({"shifts.view", "shifts.manage"}))
        shift = controller.create_shift(
            name="وردية عامة",
            start_time=time(8, 0),
            end_time=time(16, 0),
            working_days=["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday"],
        )
        assert shift is not None

        # Explicitly assign only one of the two employees.
        assignment = controller.assign_employee(
            employee_id=assigned_id, shift_id=shift["id"], effective_from=date(2026, 1, 1)
        )
        assert assignment is not None
        assert assignment["employee_id"] == assigned_id
        assert assignment["shift_id"] == shift["id"]

        assigned_history = controller.list_assignment_history(assigned_id)
        assert len(assigned_history) == 1

        # The other employee remains entirely unassigned -- proves the
        # shift's creation never implicitly touched every employee.
        unassigned_history = controller.list_assignment_history(unassigned_id)
        assert unassigned_history == []

    def test_manager_can_explicitly_assign_the_same_shift_to_multiple_employees(
        self, qapp, company_factory
    ):
        """"Bulk" assignment is always several explicit per-employee calls,
        never one implicit "apply to all" action.
        """
        company_id = company_factory()
        with session_scope() as session:
            employee_service = EmployeeService(session, company_id=company_id)
            employee_ids = [
                employee_service.create_employee(
                    employee_number=f"BLK-{i:03d}", full_name=f"موظف {i}"
                ).id
                for i in range(3)
            ]

        controller = _controller(company_id, codes=frozenset({"shifts.view", "shifts.manage"}))
        shift = controller.create_shift(
            name="وردية جماعية", start_time=time(9, 0), end_time=time(17, 0)
        )
        assert shift is not None

        for employee_id in employee_ids:
            result = controller.assign_employee(
                employee_id=employee_id, shift_id=shift["id"], effective_from=date(2026, 2, 1)
            )
            assert result is not None
            assert result["shift_id"] == shift["id"]

        for employee_id in employee_ids:
            history = controller.list_assignment_history(employee_id)
            assert len(history) == 1
            assert history[0]["shift_id"] == shift["id"]

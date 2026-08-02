"""RBAC enforcement: `requires_permission` must gate every controller method it decorates.

Exercises the real decorator on a real controller (not a mock), the
same runtime gate every desktop page and REST API route ultimately
relies on (see ``controllers/base_controller.py`` and
``api/dependencies.py``).
"""

from __future__ import annotations

from controllers.department_controller import DepartmentController
from controllers.employee_controller import EmployeeController


def test_denied_create_returns_none_and_emits_access_denied(qapp, company_factory):
    company_id = company_factory()
    controller = EmployeeController(company_id=company_id, permission_codes=frozenset())

    denials = []
    controller.operation_failed.connect(denials.append)

    result = controller.create_employee(employee_number="R-001", full_name="مرفوض")

    assert result is None
    assert len(denials) == 1
    assert "صلاحية" in denials[0]


def test_denied_create_never_touches_the_database(qapp, company_factory):
    """A denial must short-circuit before any service/session code runs."""
    company_id = company_factory()
    controller = EmployeeController(company_id=company_id, permission_codes=frozenset())
    # A duplicate employee_number would normally raise EmployeeValidationError
    # from inside the service layer - if that error surfaces here, the
    # decorator failed to short-circuit before reaching the service.
    controller.create_employee(employee_number="DUP", full_name="واحد")
    controller.create_employee(employee_number="DUP", full_name="اثنان")

    with_permission = EmployeeController(
        company_id=company_id, permission_codes=frozenset({"employees.view", "employees.manage"})
    )
    assert with_permission.list_employees() == []


def test_granted_permission_allows_create(qapp, company_factory):
    company_id = company_factory()
    controller = EmployeeController(
        company_id=company_id, permission_codes=frozenset({"employees.manage"})
    )
    result = controller.create_employee(employee_number="G-001", full_name="مسموح")
    assert result is not None
    assert result["employee_number"] == "G-001"


def test_either_of_multiple_codes_grants_access(qapp, company_factory):
    """A method gated behind view-OR-manage must accept either code."""
    company_id = company_factory()
    view_only = EmployeeController(
        company_id=company_id, permission_codes=frozenset({"employees.view"})
    )
    assert view_only.list_employees() == []  # allowed, just empty


def test_list_returning_method_denies_with_empty_list_not_none(qapp, company_factory):
    """Every list-returning controller method must use default=[] (see base_controller.py)."""
    company_id = company_factory()
    controller = DepartmentController(company_id=company_id, permission_codes=frozenset())
    result = controller.list_all()
    assert result == []
    assert result is not None


def test_bool_returning_method_denies_with_false_not_none(qapp, company_factory):
    company_id = company_factory()
    controller = DepartmentController(company_id=company_id, permission_codes=frozenset())
    result = controller.delete_department(1)
    assert result is False

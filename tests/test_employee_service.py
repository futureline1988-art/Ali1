"""Employee/department service-layer validation: the guardrails around HR data entry."""

from __future__ import annotations

from decimal import Decimal

import pytest

from database.database import session_scope
from services.department_service import DepartmentService, DepartmentValidationError
from services.employee_service import EmployeeService, EmployeeValidationError


def test_create_employee_succeeds_with_minimal_fields(company_factory):
    company_id = company_factory()
    with session_scope() as session:
        employee = EmployeeService(session, company_id=company_id).create_employee(
            employee_number="E-001", full_name="سارة محمد"
        )
        assert employee.id is not None
        assert employee.qr_code_path is not None
        assert employee.barcode_path is not None


def test_duplicate_employee_number_rejected(company_factory):
    company_id = company_factory()
    with session_scope() as session:
        EmployeeService(session, company_id=company_id).create_employee(
            employee_number="E-002", full_name="أول"
        )
    with session_scope() as session:
        with pytest.raises(EmployeeValidationError, match="already in use"):
            EmployeeService(session, company_id=company_id).create_employee(
                employee_number="E-002", full_name="ثاني"
            )


def test_invalid_email_rejected(company_factory):
    company_id = company_factory()
    with session_scope() as session:
        with pytest.raises(EmployeeValidationError):
            EmployeeService(session, company_id=company_id).create_employee(
                employee_number="E-003", full_name="ثالث", email="not-an-email"
            )


def test_negative_salary_rejected(company_factory):
    company_id = company_factory()
    with session_scope() as session:
        with pytest.raises(EmployeeValidationError):
            EmployeeService(session, company_id=company_id).create_employee(
                employee_number="E-004", full_name="رابع", salary=Decimal("-100")
            )


def test_department_from_other_company_rejected(company_factory):
    company_a = company_factory(name="شركة أ")
    company_b = company_factory(name="شركة ب")

    with session_scope() as session:
        dept = DepartmentService(session, company_id=company_a).create_department(name="قسم أ")
        dept_id = dept.id

    with session_scope() as session:
        with pytest.raises(EmployeeValidationError):
            EmployeeService(session, company_id=company_b).create_employee(
                employee_number="E-005", full_name="خامس", department_id=dept_id
            )


def test_update_employee_applies_partial_fields(company_factory):
    company_id = company_factory()
    with session_scope() as session:
        employee = EmployeeService(session, company_id=company_id).create_employee(
            employee_number="E-006", full_name="سادس"
        )
        employee_id = employee.id

    with session_scope() as session:
        service = EmployeeService(session, company_id=company_id)
        employee = service.employee_repo.get_by_id(employee_id)
        service.update_employee(employee, position="مطور")
        assert employee.position == "مطور"
        assert employee.full_name == "سادس"  # untouched


def test_delete_employee_is_soft_delete(company_factory):
    company_id = company_factory()
    with session_scope() as session:
        employee = EmployeeService(session, company_id=company_id).create_employee(
            employee_number="E-007", full_name="سابع"
        )
        employee_id = employee.id

    with session_scope() as session:
        service = EmployeeService(session, company_id=company_id)
        employee = service.employee_repo.get_by_id(employee_id)
        service.delete_employee(employee)

    with session_scope() as session:
        service = EmployeeService(session, company_id=company_id)
        assert service.employee_repo.get_by_id(employee_id) is None
        assert service.employee_repo.get_by_id(employee_id, include_deleted=True) is not None


def test_duplicate_department_name_rejected(company_factory):
    company_id = company_factory()
    with session_scope() as session:
        DepartmentService(session, company_id=company_id).create_department(name="المالية")
    with session_scope() as session:
        with pytest.raises(DepartmentValidationError, match="already in use"):
            DepartmentService(session, company_id=company_id).create_department(name="المالية")


def test_department_cannot_become_its_own_parent(company_factory):
    company_id = company_factory()
    with session_scope() as session:
        service = DepartmentService(session, company_id=company_id)
        department = service.create_department(name="الإدارة")
        with pytest.raises(DepartmentValidationError):
            service.move_department(department, new_parent_id=department.id)

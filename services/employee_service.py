"""Employee management service: create, update, and lifecycle transitions.

Owns the business validation and QR/barcode generation that the
:class:`~models.employee.Employee` model itself deliberately does not —
that model is a pure persistence entity plus small state-derived
helpers, per this project's established layering.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from models.audit_log import AuditLog
from models.employee import Employee
from models.enums import AuditAction, EmploymentStatus
from repositories.audit_log_repository import AuditLogRepository
from repositories.department_repository import DepartmentRepository
from repositories.employee_repository import EmployeeRepository
from utils.qr_barcode import generate_employee_codes
from utils.validators import (
    is_valid_email,
    is_valid_employee_number,
    is_valid_national_id,
    is_valid_phone,
    is_valid_salary,
    is_within_length,
)

#: Fields update_employee() is allowed to mass-assign via
#: Employee.update_from_dict(); deliberately excludes employee_number
#: (changing a badge/QR-encoded identifier is a distinct, deliberate
#: operation, not a routine field edit) and employment_status (that has
#: its own dedicated state-changing methods below).
_UPDATABLE_FIELDS = frozenset(
    {
        "full_name",
        "national_id",
        "department_id",
        "position",
        "salary",
        "phone",
        "email",
        "notes",
        "hire_date",
        "branch_id",
    }
)


class EmployeeValidationError(Exception):
    """Raised when employee input fails validation or a uniqueness check."""


class EmployeeService:
    """Employee operations scoped to one company.

    Attributes:
        session: The active database session.
        company_id: The company this service operates within.
        actor_user_id: The user performing these operations, recorded
            on every audit log entry; ``None`` for system-initiated
            changes.
    """

    def __init__(
        self, session: Session, *, company_id: int, actor_user_id: int | None = None
    ) -> None:
        """Create an employee service bound to one session and company.

        Args:
            session: The active database session.
            company_id: The company to operate within.
            actor_user_id: The acting user's id, for audit attribution.
        """
        self.session = session
        self.company_id = company_id
        self.actor_user_id = actor_user_id
        self.employee_repo = EmployeeRepository(session, company_id=company_id)
        self.department_repo = DepartmentRepository(session, company_id=company_id)
        self.audit_repo = AuditLogRepository(session)

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
    ) -> Employee:
        """Create a new employee, generating their QR code and barcode.

        Args:
            employee_number: Unique-per-company business identifier.
            full_name: The employee's full name.
            department_id: The employee's department, if assigned.
            branch_id: The employee's branch, if the company tracks it.
            national_id: National ID number, if provided.
            email: Contact email, if provided.
            phone: Contact phone number, if provided.
            position: Job title, if provided.
            salary: Base salary, if provided.
            hire_date: Date the employee joined, if provided.
            notes: Free-form HR notes.

        Returns:
            The newly created, persisted employee, with
            :attr:`~models.employee.Employee.qr_code_path` and
            :attr:`~models.employee.Employee.barcode_path` populated.

        Raises:
            EmployeeValidationError: If any field fails format
                validation, if ``department_id`` does not resolve to a
                department in this company, or if ``employee_number``
                /``national_id`` is already in use within this company.
        """
        self._validate_fields(
            employee_number=employee_number,
            full_name=full_name,
            national_id=national_id,
            email=email,
            phone=phone,
            salary=salary,
        )
        self._validate_department(department_id)

        if self.employee_repo.get_by_employee_number(employee_number) is not None:
            raise EmployeeValidationError(
                f"Employee number {employee_number!r} is already in use."
            )
        if national_id and self.employee_repo.get_by_national_id(national_id) is not None:
            raise EmployeeValidationError(
                f"National ID {national_id!r} is already in use."
            )

        employee = Employee(
            company_id=self.company_id,
            branch_id=branch_id,
            employee_number=employee_number,
            full_name=full_name,
            department_id=department_id,
            national_id=national_id,
            email=email,
            phone=phone,
            position=position,
            salary=salary,
            hire_date=hire_date,
            notes=notes,
            created_by_id=self.actor_user_id,
        )
        self.employee_repo.add(employee)

        qr_path, barcode_path = generate_employee_codes(
            employee_number=employee_number, public_id=str(employee.public_id)
        )
        employee.qr_code_path = str(qr_path)
        employee.barcode_path = str(barcode_path)
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.CREATE,
                entity_type="Employee",
                entity_id=employee.id,
                description=f"Created employee {employee_number!r} ({full_name!r}).",
            )
        )
        return employee

    def update_employee(self, employee: Employee, **fields: Any) -> Employee:
        """Update an existing employee's editable fields.

        Args:
            employee: The employee to update (must belong to this
                service's company).
            **fields: Any subset of the updatable fields (see
                :data:`_UPDATABLE_FIELDS`); unrecognized keys are
                silently ignored, matching
                :meth:`~models.base.SerializationMixin.update_from_dict`.

        Returns:
            The updated employee.

        Raises:
            EmployeeValidationError: If a provided field fails
                validation, or a provided ``national_id`` collides with
                a different employee in this company.
        """
        if employee.company_id != self.company_id:
            raise EmployeeValidationError(
                "This employee does not belong to the current company."
            )

        if "full_name" in fields and not is_within_length(
            fields["full_name"], minimum=2, maximum=150
        ):
            raise EmployeeValidationError("Full name must be 2-150 characters.")
        if fields.get("national_id"):
            if not is_valid_national_id(fields["national_id"]):
                raise EmployeeValidationError("Invalid national ID format.")
            existing = self.employee_repo.get_by_national_id(fields["national_id"])
            if existing is not None and existing.id != employee.id:
                raise EmployeeValidationError(
                    f"National ID {fields['national_id']!r} is already in use."
                )
        if fields.get("email") and not is_valid_email(fields["email"]):
            raise EmployeeValidationError("Invalid email format.")
        if fields.get("phone") and not is_valid_phone(fields["phone"]):
            raise EmployeeValidationError("Invalid phone number format.")
        if fields.get("salary") is not None and not is_valid_salary(fields["salary"]):
            raise EmployeeValidationError("Invalid salary amount.")
        if "department_id" in fields:
            self._validate_department(fields["department_id"])

        employee.update_from_dict(fields, allowed_fields=_UPDATABLE_FIELDS)
        employee.updated_by_id = self.actor_user_id
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="Employee",
                entity_id=employee.id,
                description=f"Updated employee {employee.employee_number!r}.",
                changes={key: str(value) for key, value in fields.items()},
            )
        )
        return employee

    def set_employment_status(
        self, employee: Employee, status: EmploymentStatus
    ) -> Employee:
        """Change an employee's employment status.

        Args:
            employee: The employee to update.
            status: The new :class:`~models.enums.EmploymentStatus`.

        Returns:
            The updated employee.
        """
        previous = employee.employment_status
        employee.employment_status = status
        employee.updated_by_id = self.actor_user_id
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="Employee",
                entity_id=employee.id,
                description=(
                    f"Changed employment status of {employee.employee_number!r} "
                    f"from {previous.value!r} to {status.value!r}."
                ),
            )
        )
        return employee

    def delete_employee(self, employee: Employee) -> None:
        """Soft-delete an employee.

        Args:
            employee: The employee to remove from active views.
        """
        self.employee_repo.delete(employee)
        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.DELETE,
                entity_type="Employee",
                entity_id=employee.id,
                description=f"Deleted employee {employee.employee_number!r}.",
            )
        )

    def list_employees(
        self, *, department_id: int | None = None, active_only: bool = False
    ) -> list[Employee]:
        """List employees, optionally filtered.

        Args:
            department_id: Restrict to one department, if given.
            active_only: Restrict to
                :attr:`~models.enums.EmploymentStatus.ACTIVE` employees.

        Returns:
            Matching employees.
        """
        if department_id is not None:
            employees = self.employee_repo.list_by_department(department_id)
            if active_only:
                return [e for e in employees if e.is_currently_active]
            return employees
        if active_only:
            return self.employee_repo.list_active()
        return self.employee_repo.list_all()

    def _validate_fields(
        self,
        *,
        employee_number: str,
        full_name: str,
        national_id: str | None,
        email: str | None,
        phone: str | None,
        salary: Decimal | None,
    ) -> None:
        """Validate the format of every provided field, raising on the first failure."""
        if not is_valid_employee_number(employee_number):
            raise EmployeeValidationError(
                f"Invalid employee number format: {employee_number!r}."
            )
        if not is_within_length(full_name, minimum=2, maximum=150):
            raise EmployeeValidationError("Full name must be 2-150 characters.")
        if national_id and not is_valid_national_id(national_id):
            raise EmployeeValidationError(f"Invalid national ID format: {national_id!r}.")
        if email and not is_valid_email(email):
            raise EmployeeValidationError(f"Invalid email format: {email!r}.")
        if phone and not is_valid_phone(phone):
            raise EmployeeValidationError(f"Invalid phone number format: {phone!r}.")
        if salary is not None and not is_valid_salary(salary):
            raise EmployeeValidationError(f"Invalid salary amount: {salary!r}.")

    def _validate_department(self, department_id: int | None) -> None:
        """Verify a department id resolves to a department in this company."""
        if department_id is None:
            return
        if self.department_repo.get_by_id(department_id) is None:
            raise EmployeeValidationError(
                f"Department {department_id!r} was not found in this company."
            )

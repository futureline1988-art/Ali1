"""Department management service: create, update, and re-parenting.

Owns the department-hierarchy invariants that
:class:`~models.department.Department`'s own docstring explicitly
defers to the service layer: never introducing a cycle, and never
assigning a parent from a different company.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from models.audit_log import AuditLog
from models.department import Department
from models.enums import AuditAction
from repositories.audit_log_repository import AuditLogRepository
from repositories.department_repository import DepartmentRepository
from utils.validators import is_within_length

_UPDATABLE_FIELDS = frozenset({"name", "code", "description", "is_active"})


class DepartmentValidationError(Exception):
    """Raised when department input fails validation or would corrupt the hierarchy."""


class DepartmentService:
    """Department operations scoped to one company.

    Attributes:
        session: The active database session.
        company_id: The company this service operates within.
        actor_user_id: The user performing these operations, recorded
            on every audit log entry.
    """

    def __init__(
        self, session: Session, *, company_id: int, actor_user_id: int | None = None
    ) -> None:
        """Create a department service bound to one session and company.

        Args:
            session: The active database session.
            company_id: The company to operate within.
            actor_user_id: The acting user's id, for audit attribution.
        """
        self.session = session
        self.company_id = company_id
        self.actor_user_id = actor_user_id
        self.department_repo = DepartmentRepository(session, company_id=company_id)
        self.audit_repo = AuditLogRepository(session)

    def create_department(
        self,
        *,
        name: str,
        code: str | None = None,
        description: str | None = None,
        parent_department_id: int | None = None,
    ) -> Department:
        """Create a new department.

        Args:
            name: Department name; must be unique within this company.
            code: Optional short code; must be unique within this
                company when provided.
            description: Optional free-form description.
            parent_department_id: Optional parent department, which
                must belong to this same company.

        Returns:
            The newly created, persisted department.

        Raises:
            DepartmentValidationError: If ``name`` fails length
                validation, if ``name``/``code`` is already in use, or
                if ``parent_department_id`` does not resolve to a
                department in this company.
        """
        if not is_within_length(name, minimum=2, maximum=150):
            raise DepartmentValidationError("Department name must be 2-150 characters.")
        if self.department_repo.get_by_name(name) is not None:
            raise DepartmentValidationError(f"Department name {name!r} is already in use.")
        if code and self.department_repo.get_by_code(code) is not None:
            raise DepartmentValidationError(f"Department code {code!r} is already in use.")

        parent = self._resolve_parent(parent_department_id)

        department = Department(
            company_id=self.company_id,
            name=name,
            code=code,
            description=description,
            parent_department_id=parent.id if parent is not None else None,
            created_by_id=self.actor_user_id,
        )
        self.department_repo.add(department)

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.CREATE,
                entity_type="Department",
                entity_id=department.id,
                description=f"Created department {name!r}.",
            )
        )
        return department

    def update_department(self, department: Department, **fields: object) -> Department:
        """Update a department's editable fields (not its parent — see :meth:`move_department`).

        Args:
            department: The department to update (must belong to this
                service's company).
            **fields: Any subset of ``name``/``code``/``description``/
                ``is_active``; unrecognized keys are ignored.

        Returns:
            The updated department.

        Raises:
            DepartmentValidationError: If a provided ``name``/``code``
                collides with a different department in this company.
        """
        if department.company_id != self.company_id:
            raise DepartmentValidationError(
                "This department does not belong to the current company."
            )
        if "name" in fields:
            if not is_within_length(str(fields["name"]), minimum=2, maximum=150):
                raise DepartmentValidationError("Department name must be 2-150 characters.")
            existing = self.department_repo.get_by_name(str(fields["name"]))
            if existing is not None and existing.id != department.id:
                raise DepartmentValidationError(
                    f"Department name {fields['name']!r} is already in use."
                )
        if fields.get("code"):
            existing_code = self.department_repo.get_by_code(str(fields["code"]))
            if existing_code is not None and existing_code.id != department.id:
                raise DepartmentValidationError(
                    f"Department code {fields['code']!r} is already in use."
                )

        department.update_from_dict(fields, allowed_fields=_UPDATABLE_FIELDS)
        department.updated_by_id = self.actor_user_id
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="Department",
                entity_id=department.id,
                description=f"Updated department {department.name!r}.",
                changes={key: str(value) for key, value in fields.items()},
            )
        )
        return department

    def move_department(
        self, department: Department, *, new_parent_id: int | None
    ) -> Department:
        """Re-parent a department, guarding against cycles and cross-company parents.

        Args:
            department: The department to move.
            new_parent_id: The new parent's id, or ``None`` to make
                ``department`` a root department.

        Returns:
            The updated department.

        Raises:
            DepartmentValidationError: If ``new_parent_id`` is
                ``department.id`` itself, does not resolve to a
                department in this company, or would create a cycle
                (i.e. ``department`` is an ancestor of the proposed
                parent).
        """
        if new_parent_id is None:
            department.parent_department_id = None
            self.session.flush()
            return department

        if new_parent_id == department.id:
            raise DepartmentValidationError("A department cannot be its own parent.")

        new_parent = self._resolve_parent(new_parent_id)
        node: Department | None = new_parent
        while node is not None:
            if node.id == department.id:
                raise DepartmentValidationError(
                    "This move would create a circular department hierarchy."
                )
            node = node.parent

        department.parent_department_id = new_parent_id
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="Department",
                entity_id=department.id,
                description=f"Moved department {department.name!r} under a new parent.",
            )
        )
        return department

    def delete_department(self, department: Department) -> None:
        """Soft-delete a department.

        Child departments are not cascade-deleted (see
        :class:`~models.department.Department`'s ``ON DELETE SET NULL``)
        — they become root departments.

        Args:
            department: The department to remove from active views.
        """
        self.department_repo.delete(department)
        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.DELETE,
                entity_type="Department",
                entity_id=department.id,
                description=f"Deleted department {department.name!r}.",
            )
        )

    def list_hierarchy_roots(self) -> list[Department]:
        """List this company's top-level departments.

        Returns:
            Root departments; each has a populated
            :attr:`~models.department.Department.children` collection
            for building a tree view.
        """
        return self.department_repo.list_root_departments()

    def _resolve_parent(self, department_id: int | None) -> Department | None:
        """Fetch a department by id, ensuring it belongs to this company."""
        if department_id is None:
            return None
        department = self.department_repo.get_by_id(department_id)
        if department is None:
            raise DepartmentValidationError(
                f"Department {department_id!r} was not found in this company."
            )
        return department

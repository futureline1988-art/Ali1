"""Branch (physical location) management service.

Plain CRUD over :class:`~models.branch.Branch`, following the same
shape as :class:`~services.department_service.DepartmentService`, plus
one cross-entity invariant the model defers to the service layer: at
most one branch per company can be the designated
:attr:`~models.branch.Branch.is_main_branch` — enforced here by
unmarking the previous main branch (if any) whenever a new one is
designated, the same "close the old one before opening the new one"
pattern already used for
:meth:`~services.shift_service.ShiftService.assign_employee`.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from models.audit_log import AuditLog
from models.branch import Branch
from models.enums import AuditAction
from repositories.audit_log_repository import AuditLogRepository
from repositories.branch_repository import BranchRepository
from utils.validators import is_within_length

_UPDATABLE_FIELDS = frozenset(
    {"name", "code", "address", "phone", "is_main_branch", "is_active"}
)


class BranchValidationError(Exception):
    """Raised when branch input fails validation."""


class BranchService:
    """Branch operations scoped to one company.

    Attributes:
        session: The active database session.
        company_id: The company this service operates within.
        actor_user_id: The user performing these operations, recorded
            on every audit log entry.
    """

    def __init__(
        self, session: Session, *, company_id: int, actor_user_id: int | None = None
    ) -> None:
        """Create a branch service bound to one session and company.

        Args:
            session: The active database session.
            company_id: The company to operate within.
            actor_user_id: The acting user's id, for audit attribution.
        """
        self.session = session
        self.company_id = company_id
        self.actor_user_id = actor_user_id
        self.branch_repo = BranchRepository(session, company_id=company_id)
        self.audit_repo = AuditLogRepository(session)

    def create_branch(
        self,
        *,
        name: str,
        code: str | None = None,
        address: str | None = None,
        phone: str | None = None,
        is_main_branch: bool = False,
        is_active: bool = True,
    ) -> Branch:
        """Create a new branch.

        Args:
            name: Branch name; must be unique within this company.
            code: Optional short code; must be unique within this
                company when provided.
            address: Optional branch address.
            phone: Optional contact phone.
            is_main_branch: Whether this becomes the company's main
                branch; if ``True``, any previously-designated main
                branch is unmarked.
            is_active: Whether the branch is currently operating.

        Returns:
            The newly created, persisted branch.

        Raises:
            BranchValidationError: If ``name`` fails length validation,
                or if ``name``/``code`` is already in use.
        """
        if not is_within_length(name, minimum=2, maximum=150):
            raise BranchValidationError("Branch name must be 2-150 characters.")
        existing_by_name = self.branch_repo.list_all()
        if any(branch.name == name for branch in existing_by_name):
            raise BranchValidationError(f"Branch name {name!r} is already in use.")
        if code and self.branch_repo.get_by_code(code) is not None:
            raise BranchValidationError(f"Branch code {code!r} is already in use.")

        if is_main_branch:
            self._unmark_current_main_branch()

        branch = Branch(
            company_id=self.company_id,
            name=name,
            code=code,
            address=address,
            phone=phone,
            is_main_branch=is_main_branch,
            is_active=is_active,
            created_by_id=self.actor_user_id,
        )
        self.branch_repo.add(branch)

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.CREATE,
                entity_type="Branch",
                entity_id=branch.id,
                description=f"Created branch {name!r}.",
            )
        )
        return branch

    def update_branch(self, branch: Branch, **fields: object) -> Branch:
        """Update a branch's editable fields.

        Args:
            branch: The branch to update (must belong to this
                service's company).
            **fields: Any subset of ``name``/``code``/``address``/
                ``phone``/``is_main_branch``/``is_active``;
                unrecognized keys are ignored.

        Returns:
            The updated branch.

        Raises:
            BranchValidationError: If a provided ``name`` fails length
                validation, or a provided ``name``/``code`` collides
                with a different branch in this company.
        """
        if branch.company_id != self.company_id:
            raise BranchValidationError("This branch does not belong to the current company.")
        if "name" in fields:
            if not is_within_length(str(fields["name"]), minimum=2, maximum=150):
                raise BranchValidationError("Branch name must be 2-150 characters.")
            collision = next(
                (
                    existing
                    for existing in self.branch_repo.list_all()
                    if existing.name == fields["name"] and existing.id != branch.id
                ),
                None,
            )
            if collision is not None:
                raise BranchValidationError(f"Branch name {fields['name']!r} is already in use.")
        if fields.get("code"):
            existing_code = self.branch_repo.get_by_code(str(fields["code"]))
            if existing_code is not None and existing_code.id != branch.id:
                raise BranchValidationError(f"Branch code {fields['code']!r} is already in use.")

        if fields.get("is_main_branch") is True and not branch.is_main_branch:
            self._unmark_current_main_branch()

        branch.update_from_dict(fields, allowed_fields=_UPDATABLE_FIELDS)
        branch.updated_by_id = self.actor_user_id
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="Branch",
                entity_id=branch.id,
                description=f"Updated branch {branch.name!r}.",
                changes={key: str(value) for key, value in fields.items()},
            )
        )
        return branch

    def delete_branch(self, branch: Branch) -> None:
        """Soft-delete a branch.

        Employees/devices assigned to this branch are left untouched
        (``branch_id`` uses ``ON DELETE SET NULL`` at the database
        level for a hard delete, but a soft delete does not even
        trigger that — the branch simply stops being selectable for
        new assignments).

        Args:
            branch: The branch to remove from active views.
        """
        self.branch_repo.delete(branch)
        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.DELETE,
                entity_type="Branch",
                entity_id=branch.id,
                description=f"Deleted branch {branch.name!r}.",
            )
        )

    def list_all(self) -> list[Branch]:
        """List every branch defined for this company.

        Returns:
            Branches ordered by id.
        """
        return self.branch_repo.list_all()

    def get_main_branch(self) -> Branch | None:
        """Fetch this company's designated main branch, if any.

        Returns:
            The main branch, or ``None``.
        """
        return self.branch_repo.get_main_branch()

    def _unmark_current_main_branch(self) -> None:
        """Clear ``is_main_branch`` on whichever branch currently holds it, if any."""
        current_main = self.branch_repo.get_main_branch()
        if current_main is not None:
            current_main.is_main_branch = False
            self.session.flush()

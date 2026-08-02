"""Branch controller: bridges the branches screen to ``BranchService``."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from sqlalchemy.orm import Session

from controllers.base_controller import BaseController, requires_permission
from repositories.branch_repository import BranchRepository
from services.branch_service import BranchService


class BranchController(BaseController):
    """Controller for the branches management screen."""

    branches_changed = Signal()
    """Emitted after any successful create/update/delete."""

    @requires_permission("branches.manage")
    def create_branch(self, **fields: Any) -> dict[str, Any] | None:
        """Create a new branch.

        Args mirror :meth:`~services.branch_service.BranchService.create_branch`.

        Returns:
            The new branch's data as a dict, or ``None`` on failure.
        """

        def do_create(session: Session) -> dict[str, Any]:
            service = BranchService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            branch = service.create_branch(**fields)
            return branch.to_dict()

        result = self._run(do_create)
        if result is not None:
            self.branches_changed.emit()
        return result

    @requires_permission("branches.manage")
    def update_branch(self, branch_id: int, **fields: Any) -> dict[str, Any] | None:
        """Update a branch's editable fields.

        Args:
            branch_id: The branch to update.
            **fields: See
                :meth:`~services.branch_service.BranchService.update_branch`.

        Returns:
            The updated branch's data as a dict, or ``None`` on
            failure.
        """

        def do_update(session: Session) -> dict[str, Any]:
            repo = BranchRepository(session, company_id=self.company_id)
            branch = repo.get_by_id(branch_id)
            if branch is None:
                raise ValueError(f"Branch {branch_id!r} was not found.")
            service = BranchService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            updated = service.update_branch(branch, **fields)
            return updated.to_dict()

        result = self._run(do_update)
        if result is not None:
            self.branches_changed.emit()
        return result

    @requires_permission("branches.manage", default=False)
    def delete_branch(self, branch_id: int) -> bool:
        """Soft-delete a branch.

        Args:
            branch_id: The branch to delete.

        Returns:
            ``True`` on success, ``False`` on failure.
        """

        def do_delete(session: Session) -> bool:
            repo = BranchRepository(session, company_id=self.company_id)
            branch = repo.get_by_id(branch_id)
            if branch is None:
                raise ValueError(f"Branch {branch_id!r} was not found.")
            service = BranchService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            service.delete_branch(branch)
            return True

        result = self._run(do_delete)
        if result:
            self.branches_changed.emit()
        return bool(result)

    @requires_permission("branches.view", "branches.manage", default=[])
    def list_branches(self) -> list[dict[str, Any]]:
        """List every branch defined for this company.

        Returns:
            Branches' data as dicts; an empty list on failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            service = BranchService(session, company_id=self.company_id)
            return [branch.to_dict() for branch in service.list_all()]

        return self._run(do_list) or []

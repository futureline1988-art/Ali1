"""Repository for :class:`~models.branch.Branch`."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.branch import Branch
from repositories.base_repository import CompanyScopedRepository


class BranchRepository(CompanyScopedRepository[Branch]):
    """Data access for :class:`~models.branch.Branch`, scoped to one company."""

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create a repository bound to ``session`` and ``company_id``."""
        super().__init__(session, model=Branch, company_id=company_id)

    def get_by_code(self, code: str) -> Branch | None:
        """Fetch a branch by its unique-per-company short code.

        Args:
            code: The branch's :attr:`~models.branch.Branch.code`.

        Returns:
            The matching branch, or ``None``.
        """
        statement = select(Branch).where(
            Branch.company_id == self.company_id,
            Branch.code == code,
            Branch.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

    def get_main_branch(self) -> Branch | None:
        """Fetch this company's designated main/head-office branch.

        Returns:
            The branch with
            :attr:`~models.branch.Branch.is_main_branch` set, or
            ``None`` if none is designated.
        """
        statement = select(Branch).where(
            Branch.company_id == self.company_id,
            Branch.is_main_branch.is_(True),
            Branch.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

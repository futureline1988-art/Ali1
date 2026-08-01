"""Repositories for :class:`~models.leave.LeavePolicy` and
:class:`~models.leave.LeaveRequest`.

Grouped in one file, mirroring how the two models are grouped in
``models/leave.py``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.enums import LeaveStatus, LeaveType
from models.leave import LeavePolicy, LeaveRequest
from repositories.base_repository import CompanyScopedRepository


class LeavePolicyRepository(CompanyScopedRepository[LeavePolicy]):
    """Data access for :class:`~models.leave.LeavePolicy`."""

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create a repository bound to ``session`` and ``company_id``."""
        super().__init__(session, model=LeavePolicy, company_id=company_id)

    def get_by_leave_type(self, leave_type: LeaveType) -> LeavePolicy | None:
        """Fetch this company's policy for one leave type.

        Args:
            leave_type: The :class:`~models.enums.LeaveType` to look up.

        Returns:
            The matching policy, or ``None`` if this company has not
            configured that leave type yet.
        """
        statement = select(LeavePolicy).where(
            LeavePolicy.company_id == self.company_id,
            LeavePolicy.leave_type == leave_type,
            LeavePolicy.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

    def list_active(self) -> list[LeavePolicy]:
        """List every leave policy currently open for requests.

        Returns:
            Policies with :attr:`~models.leave.LeavePolicy.is_active`
            set.
        """
        statement = select(LeavePolicy).where(
            LeavePolicy.company_id == self.company_id,
            LeavePolicy.is_active.is_(True),
            LeavePolicy.is_deleted.is_(False),
        )
        return list(self.session.execute(statement).scalars().all())


class LeaveRequestRepository(CompanyScopedRepository[LeaveRequest]):
    """Data access for :class:`~models.leave.LeaveRequest`."""

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create a repository bound to ``session`` and ``company_id``."""
        super().__init__(session, model=LeaveRequest, company_id=company_id)

    def list_pending(self) -> list[LeaveRequest]:
        """List every request awaiting a decision.

        Returns:
            Requests with :attr:`~models.leave.LeaveRequest.status`
            equal to :attr:`~models.enums.LeaveStatus.PENDING`, oldest
            first (an approval queue).
        """
        statement = (
            select(LeaveRequest)
            .where(
                LeaveRequest.company_id == self.company_id,
                LeaveRequest.status == LeaveStatus.PENDING,
                LeaveRequest.is_deleted.is_(False),
            )
            .order_by(LeaveRequest.created_at)
        )
        return list(self.session.execute(statement).scalars().all())

    def list_for_employee(self, employee_id: int) -> list[LeaveRequest]:
        """List one employee's leave request history.

        Args:
            employee_id: The employee's id.

        Returns:
            Requests ordered from most to least recent.
        """
        statement = (
            select(LeaveRequest)
            .where(
                LeaveRequest.company_id == self.company_id,
                LeaveRequest.employee_id == employee_id,
                LeaveRequest.is_deleted.is_(False),
            )
            .order_by(LeaveRequest.start_date.desc())
        )
        return list(self.session.execute(statement).scalars().all())

"""Leave management service: leave policies and the leave-request workflow.

Two related responsibilities, mirroring how the two models are grouped
in ``models/leave.py``: CRUD on company-configured
:class:`~models.leave.LeavePolicy` rows, and the employee-facing
submit -> approve/reject/cancel workflow for
:class:`~models.leave.LeaveRequest`. The workflow transitions themselves
(valid-state checks) live on the model (see
:meth:`~models.leave.LeaveRequest.approve` etc.); this service adds the
cross-entity validation the model cannot do on its own — resolving and
checking company ownership of the employee/policy, and computing
``days_count`` by excluding the company's holiday calendar from the
requested date range, exactly as the model's docstring defers to the
service layer.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from models.audit_log import AuditLog
from models.enums import AuditAction, LeaveType
from models.leave import LeavePolicy, LeaveRequest
from repositories.audit_log_repository import AuditLogRepository
from repositories.employee_repository import EmployeeRepository
from repositories.holiday_repository import HolidayRepository
from repositories.leave_repository import LeavePolicyRepository, LeaveRequestRepository
from utils.validators import is_within_length

_UPDATABLE_POLICY_FIELDS = frozenset(
    {"name", "annual_entitlement_days", "is_paid", "requires_approval", "is_active"}
)


class LeaveValidationError(Exception):
    """Raised when leave-policy or leave-request input fails validation."""


class LeaveService:
    """Leave operations scoped to one company.

    Attributes:
        session: The active database session.
        company_id: The company this service operates within.
        actor_user_id: The user performing these operations, recorded
            on every audit log entry.
    """

    def __init__(
        self, session: Session, *, company_id: int, actor_user_id: int | None = None
    ) -> None:
        """Create a leave service bound to one session and company.

        Args:
            session: The active database session.
            company_id: The company to operate within.
            actor_user_id: The acting user's id, for audit attribution.
        """
        self.session = session
        self.company_id = company_id
        self.actor_user_id = actor_user_id
        self.policy_repo = LeavePolicyRepository(session, company_id=company_id)
        self.request_repo = LeaveRequestRepository(session, company_id=company_id)
        self.employee_repo = EmployeeRepository(session, company_id=company_id)
        self.holiday_repo = HolidayRepository(session, company_id=company_id)
        self.audit_repo = AuditLogRepository(session)

    # ------------------------------------------------------------------
    # Leave policies
    # ------------------------------------------------------------------

    def create_policy(
        self,
        *,
        leave_type: LeaveType,
        name: str,
        annual_entitlement_days: int | None = None,
        is_paid: bool = True,
        requires_approval: bool = True,
        is_active: bool = True,
    ) -> LeavePolicy:
        """Create this company's policy for one leave type.

        Args:
            leave_type: Which :class:`~models.enums.LeaveType` this
                configures; only one policy per type per company.
            name: Display name for this policy.
            annual_entitlement_days: Days granted per year; ``None`` for
                not tracked/unlimited.
            is_paid: Whether time off under this policy is paid.
            requires_approval: Whether a request under this policy must
                be reviewed before taking effect.
            is_active: Whether employees can submit requests under this
                policy immediately.

        Returns:
            The newly created, persisted policy.

        Raises:
            LeaveValidationError: If ``name`` fails length validation,
                ``annual_entitlement_days`` is negative, or this company
                already has a policy for ``leave_type``.
        """
        if not is_within_length(name, minimum=2, maximum=150):
            raise LeaveValidationError("Leave policy name must be 2-150 characters.")
        if annual_entitlement_days is not None and annual_entitlement_days < 0:
            raise LeaveValidationError("Annual entitlement days cannot be negative.")
        if self.policy_repo.get_by_leave_type(leave_type) is not None:
            raise LeaveValidationError(
                f"A leave policy for {leave_type.value!r} already exists in this company."
            )

        policy = LeavePolicy(
            company_id=self.company_id,
            leave_type=leave_type,
            name=name,
            annual_entitlement_days=annual_entitlement_days,
            is_paid=is_paid,
            requires_approval=requires_approval,
            is_active=is_active,
            created_by_id=self.actor_user_id,
        )
        self.policy_repo.add(policy)

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.CREATE,
                entity_type="LeavePolicy",
                entity_id=policy.id,
                description=f"Created leave policy {name!r} for {leave_type.value!r}.",
            )
        )
        return policy

    def update_policy(self, policy: LeavePolicy, **fields: object) -> LeavePolicy:
        """Update a leave policy's editable fields (not its ``leave_type``).

        Args:
            policy: The policy to update (must belong to this service's
                company).
            **fields: Any subset of ``name``/``annual_entitlement_days``
                /``is_paid``/``requires_approval``/``is_active``;
                unrecognized keys are ignored.

        Returns:
            The updated policy.

        Raises:
            LeaveValidationError: If a provided ``name`` fails length
                validation or ``annual_entitlement_days`` is negative.
        """
        if policy.company_id != self.company_id:
            raise LeaveValidationError("This leave policy does not belong to the current company.")
        if "name" in fields and not is_within_length(
            str(fields["name"]), minimum=2, maximum=150
        ):
            raise LeaveValidationError("Leave policy name must be 2-150 characters.")
        entitlement = fields.get("annual_entitlement_days")
        if entitlement is not None and int(entitlement) < 0:  # type: ignore[call-overload]
            raise LeaveValidationError("Annual entitlement days cannot be negative.")

        policy.update_from_dict(fields, allowed_fields=_UPDATABLE_POLICY_FIELDS)
        policy.updated_by_id = self.actor_user_id
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="LeavePolicy",
                entity_id=policy.id,
                description=f"Updated leave policy {policy.name!r}.",
                changes={key: str(value) for key, value in fields.items()},
            )
        )
        return policy

    def delete_policy(self, policy: LeavePolicy) -> None:
        """Soft-delete a leave policy.

        Existing :class:`~models.leave.LeaveRequest` rows referencing
        this policy are left untouched; the policy simply stops being
        selectable for new requests.

        Args:
            policy: The policy to remove from active views.
        """
        self.policy_repo.delete(policy)
        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.DELETE,
                entity_type="LeavePolicy",
                entity_id=policy.id,
                description=f"Deleted leave policy {policy.name!r}.",
            )
        )

    def list_policies(self) -> list[LeavePolicy]:
        """List every leave policy defined for this company.

        Returns:
            Policies ordered by id.
        """
        return self.policy_repo.list_all()

    def list_active_policies(self) -> list[LeavePolicy]:
        """List every leave policy currently open for requests.

        Returns:
            Active policies.
        """
        return self.policy_repo.list_active()

    # ------------------------------------------------------------------
    # Leave requests
    # ------------------------------------------------------------------

    def submit_request(
        self,
        *,
        employee_id: int,
        leave_policy_id: int,
        start_date: date,
        end_date: date,
        reason: str | None = None,
        notes: str | None = None,
    ) -> LeaveRequest:
        """Submit a new leave request in
        :attr:`~models.enums.LeaveStatus.PENDING` status.

        ``days_count`` is computed as the number of calendar days in
        ``[start_date, end_date]`` minus any of the company's official
        holidays that fall within that range — the model itself never
        touches the holiday calendar, per its documented contract.

        Args:
            employee_id: The requesting employee; must belong to this
                company.
            leave_policy_id: The policy to request under; must belong
                to this company and be active.
            start_date: First day of the requested leave (inclusive).
            end_date: Last day of the requested leave (inclusive); must
                not be before ``start_date``.
            reason: The employee's stated reason.
            notes: Free-form employee-side notes.

        Returns:
            The newly created, pending request.

        Raises:
            LeaveValidationError: If ``employee_id``/``leave_policy_id``
                do not resolve within this company, the policy is
                inactive, ``end_date`` is before ``start_date``, or the
                entire range falls on holidays (leaving zero leave
                days to actually request).
        """
        employee = self.employee_repo.get_by_id(employee_id)
        if employee is None:
            raise LeaveValidationError(f"Employee {employee_id!r} was not found.")
        policy = self.policy_repo.get_by_id(leave_policy_id)
        if policy is None:
            raise LeaveValidationError(f"Leave policy {leave_policy_id!r} was not found.")
        if not policy.is_active:
            raise LeaveValidationError(f"Leave policy {policy.name!r} is not currently active.")
        if end_date < start_date:
            raise LeaveValidationError("End date cannot be before start date.")

        total_days = (end_date - start_date).days + 1
        holiday_count = len(self.holiday_repo.list_between(start_date, end_date))
        days_count = total_days - holiday_count
        if days_count <= 0:
            raise LeaveValidationError(
                "The requested date range does not contain any non-holiday days."
            )

        request = LeaveRequest(
            company_id=self.company_id,
            employee_id=employee_id,
            leave_policy_id=leave_policy_id,
            start_date=start_date,
            end_date=end_date,
            days_count=days_count,
            reason=reason,
            notes=notes,
            created_by_id=self.actor_user_id,
        )
        self.request_repo.add(request)

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.CREATE,
                entity_type="LeaveRequest",
                entity_id=request.id,
                description=(
                    f"Submitted a {days_count}-day {policy.name!r} leave request for "
                    f"{employee.full_name!r} ({start_date.isoformat()}..{end_date.isoformat()})."
                ),
            )
        )
        return request

    def approve_request(
        self, request: LeaveRequest, *, reviewer_id: int, notes: str | None = None
    ) -> LeaveRequest:
        """Approve a pending leave request.

        Args:
            request: The request to approve (must belong to this
                service's company).
            reviewer_id: The reviewing user's id.
            notes: Optional reviewer notes.

        Returns:
            The approved request.

        Raises:
            LeaveValidationError: If ``request`` does not belong to
                this company, or is not currently pending (see
                :meth:`~models.leave.LeaveRequest.approve`).
        """
        self._check_ownership(request)
        try:
            request.approve(reviewer_id, notes)
        except ValueError as exc:
            raise LeaveValidationError(str(exc)) from exc
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="LeaveRequest",
                entity_id=request.id,
                description=f"Approved leave request #{request.id}.",
            )
        )
        return request

    def reject_request(
        self, request: LeaveRequest, *, reviewer_id: int, notes: str | None = None
    ) -> LeaveRequest:
        """Reject a pending leave request.

        Args:
            request: The request to reject (must belong to this
                service's company).
            reviewer_id: The reviewing user's id.
            notes: Optional reviewer notes (typically the rejection
                reason).

        Returns:
            The rejected request.

        Raises:
            LeaveValidationError: If ``request`` does not belong to
                this company, or is not currently pending (see
                :meth:`~models.leave.LeaveRequest.reject`).
        """
        self._check_ownership(request)
        try:
            request.reject(reviewer_id, notes)
        except ValueError as exc:
            raise LeaveValidationError(str(exc)) from exc
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="LeaveRequest",
                entity_id=request.id,
                description=f"Rejected leave request #{request.id}.",
            )
        )
        return request

    def cancel_request(self, request: LeaveRequest) -> LeaveRequest:
        """Cancel a still-pending leave request.

        Args:
            request: The request to cancel (must belong to this
                service's company).

        Returns:
            The cancelled request.

        Raises:
            LeaveValidationError: If ``request`` does not belong to
                this company, or has already been reviewed/cancelled
                (see :meth:`~models.leave.LeaveRequest.cancel`).
        """
        self._check_ownership(request)
        try:
            request.cancel()
        except ValueError as exc:
            raise LeaveValidationError(str(exc)) from exc
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="LeaveRequest",
                entity_id=request.id,
                description=f"Cancelled leave request #{request.id}.",
            )
        )
        return request

    def list_requests_for_employee(self, employee_id: int) -> list[LeaveRequest]:
        """List one employee's leave request history.

        Args:
            employee_id: The employee's id.

        Returns:
            Requests ordered from most to least recent.
        """
        return self.request_repo.list_for_employee(employee_id)

    def list_pending_requests(self) -> list[LeaveRequest]:
        """List every request awaiting a decision, oldest first.

        Returns:
            Pending requests.
        """
        return self.request_repo.list_pending()

    def list_all_requests(self) -> list[LeaveRequest]:
        """List every leave request in this company.

        Returns:
            Requests ordered by id.
        """
        return self.request_repo.list_all()

    def _check_ownership(self, request: LeaveRequest) -> None:
        """Raise if ``request`` does not belong to this service's company."""
        if request.company_id != self.company_id:
            raise LeaveValidationError(
                "This leave request does not belong to the current company."
            )

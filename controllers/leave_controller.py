"""Leave controller: bridges the leave screen to ``LeaveService``."""

from __future__ import annotations

from datetime import date
from typing import Any

from PySide6.QtCore import Signal
from sqlalchemy.orm import Session

from controllers.base_controller import BaseController, requires_permission
from models.enums import LeaveType
from models.leave import LeavePolicy, LeaveRequest
from repositories.leave_repository import LeavePolicyRepository, LeaveRequestRepository
from services.leave_service import LeaveService


def _policy_to_dict(policy: LeavePolicy) -> dict[str, Any]:
    """Serialize a leave policy plus its bilingual leave-type labels."""
    data = policy.to_dict()
    data["leave_type_label_ar"] = policy.leave_type.label_ar
    data["leave_type_label_en"] = policy.leave_type.label_en
    return data


def _request_to_dict(request: LeaveRequest) -> dict[str, Any]:
    """Serialize a leave request plus display-friendly related fields."""
    data = request.to_dict()
    data["employee_name"] = request.employee.full_name
    data["policy_name"] = request.leave_policy.name
    data["leave_type_label_ar"] = request.leave_policy.leave_type.label_ar
    data["status_label_ar"] = request.status_label_ar
    data["status_label_en"] = request.status_label_en
    data["reviewed_by_name"] = request.reviewed_by.full_name if request.reviewed_by else None
    return data


class LeaveController(BaseController):
    """Controller for the leave policies and leave requests screen."""

    policies_changed = Signal()
    """Emitted after any successful create/update/delete of a policy."""

    requests_changed = Signal()
    """Emitted after any successful submit/approve/reject/cancel of a request."""

    # ------------------------------------------------------------------
    # Policies
    # ------------------------------------------------------------------

    @requires_permission("leave.manage")
    def create_policy(
        self,
        *,
        leave_type: LeaveType,
        name: str,
        annual_entitlement_days: int | None = None,
        is_paid: bool = True,
        requires_approval: bool = True,
        is_active: bool = True,
    ) -> dict[str, Any] | None:
        """Create a new leave policy.

        Args mirror :meth:`~services.leave_service.LeaveService.create_policy`.

        Returns:
            The new policy's data as a dict, or ``None`` on failure.
        """

        def do_create(session: Session) -> dict[str, Any]:
            service = LeaveService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            policy = service.create_policy(
                leave_type=leave_type,
                name=name,
                annual_entitlement_days=annual_entitlement_days,
                is_paid=is_paid,
                requires_approval=requires_approval,
                is_active=is_active,
            )
            return _policy_to_dict(policy)

        result = self._run(do_create)
        if result is not None:
            self.policies_changed.emit()
        return result

    @requires_permission("leave.manage")
    def update_policy(self, policy_id: int, **fields: Any) -> dict[str, Any] | None:
        """Update a leave policy's editable fields.

        Args:
            policy_id: The policy to update.
            **fields: See
                :meth:`~services.leave_service.LeaveService.update_policy`.

        Returns:
            The updated policy's data as a dict, or ``None`` on
            failure.
        """

        def do_update(session: Session) -> dict[str, Any]:
            repo = LeavePolicyRepository(session, company_id=self.company_id)
            policy = repo.get_by_id(policy_id)
            if policy is None:
                raise ValueError(f"Leave policy {policy_id!r} was not found.")
            service = LeaveService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            updated = service.update_policy(policy, **fields)
            return _policy_to_dict(updated)

        result = self._run(do_update)
        if result is not None:
            self.policies_changed.emit()
        return result

    @requires_permission("leave.manage", default=False)
    def delete_policy(self, policy_id: int) -> bool:
        """Soft-delete a leave policy.

        Args:
            policy_id: The policy to delete.

        Returns:
            ``True`` on success, ``False`` on failure.
        """

        def do_delete(session: Session) -> bool:
            repo = LeavePolicyRepository(session, company_id=self.company_id)
            policy = repo.get_by_id(policy_id)
            if policy is None:
                raise ValueError(f"Leave policy {policy_id!r} was not found.")
            service = LeaveService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            service.delete_policy(policy)
            return True

        result = self._run(do_delete)
        if result:
            self.policies_changed.emit()
        return bool(result)

    @requires_permission("leave.view", "leave.manage", default=[])
    def list_policies(self) -> list[dict[str, Any]]:
        """List every leave policy defined for this company.

        Returns:
            Policies' data as dicts; an empty list on failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            service = LeaveService(session, company_id=self.company_id)
            return [_policy_to_dict(policy) for policy in service.list_policies()]

        return self._run(do_list) or []

    @requires_permission("leave.view", "leave.manage", default=[])
    def list_active_policies(self) -> list[dict[str, Any]]:
        """List every leave policy currently open for requests.

        Returns:
            Active policies' data as dicts; an empty list on failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            service = LeaveService(session, company_id=self.company_id)
            return [_policy_to_dict(policy) for policy in service.list_active_policies()]

        return self._run(do_list) or []

    # ------------------------------------------------------------------
    # Requests
    # ------------------------------------------------------------------

    @requires_permission("leave.manage")
    def submit_request(
        self,
        *,
        employee_id: int,
        leave_policy_id: int,
        start_date: date,
        end_date: date,
        reason: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any] | None:
        """Submit a new leave request.

        Args mirror :meth:`~services.leave_service.LeaveService.submit_request`.

        Returns:
            The new request's data as a dict, or ``None`` on failure.
        """

        def do_submit(session: Session) -> dict[str, Any]:
            service = LeaveService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            request = service.submit_request(
                employee_id=employee_id,
                leave_policy_id=leave_policy_id,
                start_date=start_date,
                end_date=end_date,
                reason=reason,
                notes=notes,
            )
            return _request_to_dict(request)

        result = self._run(do_submit)
        if result is not None:
            self.requests_changed.emit()
        return result

    @requires_permission("leave.manage")
    def approve_request(self, request_id: int, *, notes: str | None = None) -> dict[str, Any] | None:
        """Approve a pending leave request as the current user.

        Args:
            request_id: The request to approve.
            notes: Optional reviewer notes.

        Returns:
            The approved request's data as a dict, or ``None`` on
            failure.
        """

        def do_approve(session: Session) -> dict[str, Any]:
            repo = LeaveRequestRepository(session, company_id=self.company_id)
            request = repo.get_by_id(request_id)
            if request is None:
                raise ValueError(f"Leave request {request_id!r} was not found.")
            service = LeaveService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            approved = service.approve_request(
                request, reviewer_id=self.actor_user_id, notes=notes
            )
            return _request_to_dict(approved)

        result = self._run(do_approve)
        if result is not None:
            self.requests_changed.emit()
        return result

    @requires_permission("leave.manage")
    def reject_request(self, request_id: int, *, notes: str | None = None) -> dict[str, Any] | None:
        """Reject a pending leave request as the current user.

        Args:
            request_id: The request to reject.
            notes: Optional reviewer notes (typically the rejection
                reason).

        Returns:
            The rejected request's data as a dict, or ``None`` on
            failure.
        """

        def do_reject(session: Session) -> dict[str, Any]:
            repo = LeaveRequestRepository(session, company_id=self.company_id)
            request = repo.get_by_id(request_id)
            if request is None:
                raise ValueError(f"Leave request {request_id!r} was not found.")
            service = LeaveService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            rejected = service.reject_request(
                request, reviewer_id=self.actor_user_id, notes=notes
            )
            return _request_to_dict(rejected)

        result = self._run(do_reject)
        if result is not None:
            self.requests_changed.emit()
        return result

    @requires_permission("leave.manage")
    def cancel_request(self, request_id: int) -> dict[str, Any] | None:
        """Cancel a still-pending leave request.

        Args:
            request_id: The request to cancel.

        Returns:
            The cancelled request's data as a dict, or ``None`` on
            failure.
        """

        def do_cancel(session: Session) -> dict[str, Any]:
            repo = LeaveRequestRepository(session, company_id=self.company_id)
            request = repo.get_by_id(request_id)
            if request is None:
                raise ValueError(f"Leave request {request_id!r} was not found.")
            service = LeaveService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            cancelled = service.cancel_request(request)
            return _request_to_dict(cancelled)

        result = self._run(do_cancel)
        if result is not None:
            self.requests_changed.emit()
        return result

    @requires_permission("leave.view", "leave.manage", default=[])
    def list_all_requests(self) -> list[dict[str, Any]]:
        """List every leave request in this company.

        Returns:
            Requests' data as dicts; an empty list on failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            service = LeaveService(session, company_id=self.company_id)
            return [_request_to_dict(request) for request in service.list_all_requests()]

        return self._run(do_list) or []

    @requires_permission("leave.view", "leave.manage", default=[])
    def list_pending_requests(self) -> list[dict[str, Any]]:
        """List every request awaiting a decision.

        Returns:
            Pending requests' data as dicts; an empty list on failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            service = LeaveService(session, company_id=self.company_id)
            return [_request_to_dict(request) for request in service.list_pending_requests()]

        return self._run(do_list) or []

    @requires_permission("leave.view", "leave.manage", default=[])
    def list_requests_for_employee(self, employee_id: int) -> list[dict[str, Any]]:
        """List one employee's leave request history.

        Args:
            employee_id: The employee's id.

        Returns:
            Requests' data as dicts; an empty list on failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            service = LeaveService(session, company_id=self.company_id)
            return [
                _request_to_dict(request)
                for request in service.list_requests_for_employee(employee_id)
            ]

        return self._run(do_list) or []

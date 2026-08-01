"""Leave policy and leave request ORM models.

Mirrors the pattern already used for shifts and roles: a fixed enum
(:class:`~models.enums.LeaveType`) names the *kind* of leave, while a
company-configurable row — here, :class:`LeavePolicy` — carries the
actual, tenant-specific business rules (annual entitlement, whether it's
paid, whether it needs approval). :class:`LeaveRequest` is the employee
-facing record with a full submit -> approve/reject/cancel workflow.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import BaseModel, CompanyScopedMixin, UTCDateTime, enum_column_type
from models.enums import LeaveStatus, LeaveType


class LeavePolicy(CompanyScopedMixin, BaseModel):
    """A company's configuration for one kind of leave.

    Attributes:
        leave_type: Which :class:`~models.enums.LeaveType` this policy
            configures; unique within the owning company (one policy per
            leave kind per company).
        name: Display name, as the company phrases it (e.g.
            ``"الإجازة السنوية"``).
        annual_entitlement_days: Days granted per year under this
            policy; ``NULL`` means not tracked/unlimited (typical for
            unpaid leave).
        is_paid: Whether time off under this policy is paid.
        requires_approval: Whether a :class:`LeaveRequest` under this
            policy must be reviewed before taking effect.
        is_active: Whether employees can currently submit requests under
            this policy.
    """

    __table_args__ = (
        UniqueConstraint(
            "company_id", "leave_type", name="uq_leave_policies_company_id_leave_type"
        ),
    )

    leave_type: Mapped[LeaveType] = mapped_column(
        enum_column_type(LeaveType), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    annual_entitlement_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_paid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return f"<LeavePolicy id={self.id!r} leave_type={self.leave_type.value!r}>"


class LeaveRequest(CompanyScopedMixin, BaseModel):
    """An employee's request for time off under a :class:`LeavePolicy`.

    Attributes:
        employee_id: The requesting employee.
        leave_policy_id: The policy this request is made under.
        start_date: First day of the requested leave (inclusive).
        end_date: Last day of the requested leave (inclusive); must not
            be before :attr:`start_date`.
        days_count: Number of leave days being requested. Stored
            explicitly rather than always derived from the date range,
            so the service layer can exclude weekends/holidays or apply
            half-day rules without this model needing to know about
            shifts or the holiday calendar.
        reason: The employee's stated reason for the request.
        status: Current :class:`~models.enums.LeaveStatus`.
        reviewed_by_id: The :class:`~models.user.User` who approved or
            rejected this request, if any.
        reviewed_at: When the request was reviewed.
        review_notes: The reviewer's notes (e.g. a rejection reason).
        notes: Free-form employee-side notes.
        employee: The related :class:`~models.employee.Employee`.
        leave_policy: The related :class:`LeavePolicy`.
        reviewed_by: The related :class:`~models.user.User` reviewer.
    """

    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="ck_leave_requests_valid_range"),
        CheckConstraint("days_count > 0", name="ck_leave_requests_days_count_positive"),
    )

    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    leave_policy_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("leave_policies.id"), nullable=False, index=True
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    days_count: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    status: Mapped[LeaveStatus] = mapped_column(
        enum_column_type(LeaveStatus), nullable=False, default=LeaveStatus.PENDING, index=True
    )
    reviewed_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    review_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    employee: Mapped["Employee"] = relationship(  # noqa: F821
        "Employee", backref="leave_requests"
    )
    leave_policy: Mapped["LeavePolicy"] = relationship(
        "LeavePolicy", backref="leave_requests"
    )
    # foreign_keys is required: AuditMixin already gives this table its
    # own created_by_id/updated_by_id -> users.id, so without it
    # SQLAlchemy would find multiple FK paths to 'users' and refuse to
    # guess which one this relationship means.
    reviewed_by: Mapped["User | None"] = relationship(  # noqa: F821
        "User", foreign_keys="LeaveRequest.reviewed_by_id"
    )

    @property
    def is_pending(self) -> bool:
        """Whether this request is still awaiting a decision."""
        return self.status is LeaveStatus.PENDING

    @property
    def is_approved(self) -> bool:
        """Whether this request has been approved."""
        return self.status is LeaveStatus.APPROVED

    @property
    def is_rejected(self) -> bool:
        """Whether this request has been rejected."""
        return self.status is LeaveStatus.REJECTED

    @property
    def status_label_ar(self) -> str:
        """Arabic display label for :attr:`status`."""
        return self.status.label_ar

    @property
    def status_label_en(self) -> str:
        """English display label for :attr:`status`."""
        return self.status.label_en

    def approve(self, reviewer_id: int, notes: str | None = None) -> None:
        """Approve this request.

        Args:
            reviewer_id: ID of the :class:`~models.user.User` approving
                the request.
            notes: Optional reviewer notes.

        Raises:
            ValueError: If this request is not currently
                :attr:`~models.enums.LeaveStatus.PENDING`.
        """
        if self.status is not LeaveStatus.PENDING:
            raise ValueError(
                f"Cannot approve a leave request in status {self.status.value!r}; "
                "only a pending request may be approved."
            )
        self.status = LeaveStatus.APPROVED
        self.reviewed_by_id = reviewer_id
        self.reviewed_at = datetime.now(timezone.utc)
        self.review_notes = notes

    def reject(self, reviewer_id: int, notes: str | None = None) -> None:
        """Reject this request.

        Args:
            reviewer_id: ID of the :class:`~models.user.User` rejecting
                the request.
            notes: Optional reviewer notes (typically the rejection
                reason).

        Raises:
            ValueError: If this request is not currently
                :attr:`~models.enums.LeaveStatus.PENDING`.
        """
        if self.status is not LeaveStatus.PENDING:
            raise ValueError(
                f"Cannot reject a leave request in status {self.status.value!r}; "
                "only a pending request may be rejected."
            )
        self.status = LeaveStatus.REJECTED
        self.reviewed_by_id = reviewer_id
        self.reviewed_at = datetime.now(timezone.utc)
        self.review_notes = notes

    def cancel(self) -> None:
        """Cancel this request (typically employee-initiated).

        Raises:
            ValueError: If this request has already been reviewed
                (approved or rejected) or already cancelled.
        """
        if self.status is not LeaveStatus.PENDING:
            raise ValueError(
                f"Cannot cancel a leave request in status {self.status.value!r}; "
                "only a pending request may be cancelled."
            )
        self.status = LeaveStatus.CANCELLED

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return (
            f"<LeaveRequest id={self.id!r} employee_id={self.employee_id!r} "
            f"{self.start_date!r}..{self.end_date!r} status={self.status.value!r}>"
        )

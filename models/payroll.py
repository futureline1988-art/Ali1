"""Payroll ORM models: automatic rules, adjustments, and monthly runs.

Four concerns, four tables:

* :class:`PayrollAutomaticRule` — per-company, per-rule-type on/off
  switch and calculation method for automatic deductions/additions
  (lateness, early leave, absence, missing punches, overtime). Every
  rule is disabled by default: attendance facts (late minutes, absence
  days, ...) are always recorded by
  :mod:`services.attendance_service` regardless of these rules; a rule
  only controls whether that fact ever turns into a salary adjustment.
* :class:`PayrollAdjustment` — the actual ledger of deductions/bonuses
  applied to an employee for a given month, whether generated
  automatically from a rule or entered manually by a manager. This is
  the audit trail: :class:`~models.base.AuditMixin` (via
  :class:`~models.base.BaseModel`) already gives every row
  ``created_by_id``/``updated_by_id``, and
  :class:`~models.base.SoftDeleteMixin` already gives it a
  non-destructive "cancel" (``is_deleted``/``deleted_at``) — no
  duplicate bookkeeping columns needed.
* :class:`PayrollRun` — one row per company per calendar month,
  tracking the run's lifecycle (draft -> reviewed -> finalized) and,
  if it was ever reopened after finalization, who did that and when.
* :class:`PayrollRunLine` — one computed row per employee per run: the
  actual salary breakdown (base, deductions, bonuses, net) as of the
  last computation. Mutable while the run is draft/reviewed, and only
  ever recomputed in place -- the row is NOT itself the historical
  record.
* :class:`PayrollRunSnapshot` — the historical record. Written once per
  finalize, capturing every line's figures as an immutable JSON blob.
  Later changes to salary, shift, attendance settings, or rules must
  never alter what a snapshot says already happened; correcting a
  finalized run requires an explicit reopen (see
  :mod:`services.payroll_service`), which produces a *new* snapshot on
  the next finalize rather than overwriting the old one.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import BaseModel, CompanyScopedMixin, UTCDateTime, enum_column_type
from models.encrypted_types import EncryptedDecimal
from models.enums import (
    PayrollAdjustmentSource,
    PayrollAdjustmentType,
    PayrollAutoRuleType,
    PayrollCalculationMethod,
    PayrollRunStatus,
)


class PayrollAutomaticRule(CompanyScopedMixin, BaseModel):
    """One company's configuration for one automatic payroll rule.

    Attributes:
        rule_type: Which fact this rule reacts to (lateness, early
            leave, absence, missing punch, overtime). Unique per
            company.
        enabled: Whether this rule currently turns its underlying
            attendance fact into a salary adjustment. Defaults to
            ``False`` -- attendance facts are always recorded whether
            or not any rule is enabled; only the financial consequence
            is opt-in.
        calculation_method: How :attr:`value` is applied.
        value: The method's numeric parameter -- a currency amount for
            ``FIXED_AMOUNT``/``PER_MINUTE``/``PER_HOUR``, or a
            percentage (0-100) for ``PERCENTAGE_OF_DAILY_SALARY``.
            Ignored (but still stored) for ``HALF_DAY``/``FULL_DAY``,
            which need no parameter beyond the employee's own daily
            rate.
    """

    __table_args__ = (
        UniqueConstraint(
            "company_id", "rule_type", name="uq_payroll_automatic_rules_company_rule_type"
        ),
    )

    rule_type: Mapped[PayrollAutoRuleType] = mapped_column(
        enum_column_type(PayrollAutoRuleType), nullable=False, index=True
    )
    enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    calculation_method: Mapped[PayrollCalculationMethod] = mapped_column(
        enum_column_type(PayrollCalculationMethod),
        nullable=False,
        default=PayrollCalculationMethod.FIXED_AMOUNT,
    )
    value: Mapped[Decimal] = mapped_column(
        EncryptedDecimal, nullable=False
    )

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return (
            f"<PayrollAutomaticRule id={self.id!r} rule_type={self.rule_type.value!r} "
            f"enabled={self.enabled!r}>"
        )


class PayrollAdjustment(CompanyScopedMixin, BaseModel):
    """One deduction or bonus applied to one employee for one pay period.

    Attributes:
        employee_id: The affected employee.
        adjustment_type: Deduction or bonus.
        source: Automatic (generated from a
            :class:`PayrollAutomaticRule`) or manual (entered by a
            manager).
        year: The pay period's calendar year.
        month: The pay period's calendar month (1-12).
        work_date: The specific day this adjustment pertains to, for an
            automatic, per-day adjustment (e.g. one day's lateness);
            ``NULL`` for a manual adjustment not tied to one day.
        amount: Always a positive currency amount; :attr:`adjustment_type`
            determines whether it is subtracted from or added to
            salary.
        reason_category: The stored string value of whichever reason
            enum applies -- a :class:`~models.enums.PayrollAutoRuleType`
            for an automatic deduction/addition,
            :class:`~models.enums.PayrollDeductionReason` for a manual
            deduction, or :class:`~models.enums.PayrollBonusReason` for
            a manual bonus.
        reason_text: Human-readable reason, always present (either a
            generated description for an automatic adjustment, or the
            manager's own text for a manual one).
        notes: Optional free-form notes.
        source_attendance_record_id: The
            :class:`~models.attendance.AttendanceRecord` this automatic
            adjustment was computed from, for audit traceability;
            ``NULL`` for manual adjustments.
        calculation_detail: For an automatic adjustment, the exact
            inputs used (e.g. ``{"late_minutes": 47, "method":
            "per_minute", "rate": "500"}``) so the audit trail shows
            not just the resulting amount but how it was derived.
        employee: The related :class:`~models.employee.Employee`.
    """

    # No CHECK on `amount`: it is an EncryptedDecimal, stored as opaque
    # ciphertext text, so a SQL-level numeric constraint on it would
    # compare strings, not numbers. Positivity is validated in
    # services/payroll_service.py before the row is ever constructed --
    # the same approach models/employee.py takes for Employee.salary.
    __table_args__ = (
        CheckConstraint("month >= 1 AND month <= 12", name="ck_payroll_adjustments_month_range"),
    )

    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    adjustment_type: Mapped[PayrollAdjustmentType] = mapped_column(
        enum_column_type(PayrollAdjustmentType), nullable=False, index=True
    )
    source: Mapped[PayrollAdjustmentSource] = mapped_column(
        enum_column_type(PayrollAdjustmentSource), nullable=False, index=True
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    month: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    work_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    amount: Mapped[Decimal] = mapped_column(EncryptedDecimal, nullable=False)
    reason_category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reason_text: Mapped[str] = mapped_column(String(500), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    source_attendance_record_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("attendance_records.id", ondelete="SET NULL"), nullable=True
    )
    calculation_detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    employee: Mapped["Employee"] = relationship(  # noqa: F821
        "Employee", backref="payroll_adjustments"
    )

    @property
    def is_cancelled(self) -> bool:
        """Whether this adjustment has been cancelled (soft-deleted)."""
        return self.is_deleted

    def cancel(self) -> None:
        """Cancel this adjustment without destroying its audit trail."""
        self.soft_delete()

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return (
            f"<PayrollAdjustment id={self.id!r} employee_id={self.employee_id!r} "
            f"type={self.adjustment_type.value!r} amount={self.amount!r}>"
        )


class PayrollRun(CompanyScopedMixin, BaseModel):
    """One company's payroll run for one calendar month.

    Attributes:
        year: The pay period's calendar year.
        month: The pay period's calendar month (1-12).
        status: Draft, reviewed, or finalized.
        notes: Optional free-form notes.
        finalized_at: When this run was last finalized, if ever.
        finalized_by_id: The user who last finalized this run.
        reopened_at: When this run was last reopened after having been
            finalized, if ever.
        reopened_by_id: The user who last reopened this run.
        version: How many times this run has been finalized -- also
            the version number of its latest
            :class:`PayrollRunSnapshot`.
        lines: This run's per-employee computed rows.
    """

    __table_args__ = (
        UniqueConstraint("company_id", "year", "month", name="uq_payroll_runs_company_period"),
        CheckConstraint("month >= 1 AND month <= 12", name="ck_payroll_runs_month_range"),
    )

    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    month: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[PayrollRunStatus] = mapped_column(
        enum_column_type(PayrollRunStatus),
        nullable=False,
        default=PayrollRunStatus.DRAFT,
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    finalized_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finalized_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reopened_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    reopened_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    run_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    @property
    def is_finalized(self) -> bool:
        """Whether this run is currently locked (finalized, not reopened)."""
        return self.status is PayrollRunStatus.FINALIZED

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return (
            f"<PayrollRun id={self.id!r} {self.year!r}-{self.month!r} "
            f"status={self.status.value!r}>"
        )


class PayrollRunLine(CompanyScopedMixin, BaseModel):
    """One employee's computed payroll breakdown within one :class:`PayrollRun`.

    Recomputed in place while the owning run is draft/reviewed; once
    the run is finalized, this row's figures are also captured
    verbatim into a :class:`PayrollRunSnapshot` so they remain
    queryable even if the run is later reopened and recomputed again.

    Attributes:
        payroll_run_id: The owning run.
        employee_id: The employee this line is for.
        base_salary: The employee's monthly base salary at computation
            time.
        working_days_in_period: The company's configured working-days-
            per-month figure used to derive daily/hourly/minute rates
            for this computation.
        attendance_days: Days with a present/late/early-leave status.
        absence_days: Days marked absent.
        missing_punch_days: Days marked missing check-in or check-out.
        late_minutes_total: Sum of late minutes over the period.
        early_leave_minutes_total: Sum of early-leave minutes over the
            period.
        overtime_minutes_total: Sum of overtime minutes over the
            period.
        automatic_deduction_total: Sum of this period's non-cancelled
            automatic :class:`PayrollAdjustment` deductions.
        manual_deduction_total: Sum of this period's non-cancelled
            manual :class:`PayrollAdjustment` deductions.
        bonus_total: Sum of this period's non-cancelled bonuses
            (automatic overtime additions and manual bonuses combined).
        net_salary: ``base_salary - automatic_deduction_total -
            manual_deduction_total + bonus_total``.
        computed_at: When this line was last (re)computed.
    """

    __table_args__ = (
        UniqueConstraint(
            "payroll_run_id", "employee_id", name="uq_payroll_run_lines_run_employee"
        ),
    )

    payroll_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("payroll_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )

    base_salary: Mapped[Decimal] = mapped_column(EncryptedDecimal, nullable=False)
    working_days_in_period: Mapped[int] = mapped_column(Integer, nullable=False)
    attendance_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    absence_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    missing_punch_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    late_minutes_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    early_leave_minutes_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    overtime_minutes_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    automatic_deduction_total: Mapped[Decimal] = mapped_column(
        EncryptedDecimal, nullable=False
    )
    manual_deduction_total: Mapped[Decimal] = mapped_column(
        EncryptedDecimal, nullable=False
    )
    bonus_total: Mapped[Decimal] = mapped_column(EncryptedDecimal, nullable=False)
    net_salary: Mapped[Decimal] = mapped_column(EncryptedDecimal, nullable=False)

    computed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    payroll_run: Mapped["PayrollRun"] = relationship("PayrollRun", backref="lines")
    employee: Mapped["Employee"] = relationship("Employee")  # noqa: F821

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return (
            f"<PayrollRunLine id={self.id!r} payroll_run_id={self.payroll_run_id!r} "
            f"employee_id={self.employee_id!r} net_salary={self.net_salary!r}>"
        )


class PayrollRunSnapshot(CompanyScopedMixin, BaseModel):
    """An immutable, point-in-time record of a finalized :class:`PayrollRun`.

    Written once per finalize (see
    :meth:`~services.payroll_service.PayrollService.finalize_payroll_run`)
    and never modified afterward -- reopening and refinalizing a run
    creates a *new* snapshot at the next version rather than touching
    this one, so every past finalization remains queryable exactly as
    it was approved.

    Attributes:
        payroll_run_id: The run this snapshot captures.
        run_version: Matches :attr:`~PayrollRun.run_version` at the
            time this snapshot was written.
        snapshot_json: The full line-by-line breakdown (every
            :class:`PayrollRunLine` field, per employee) as of
            finalization, plus the rule configuration that was in
            effect.
        finalized_at: When this snapshot was written.
        finalized_by_id: The user who finalized the run at this
            version.
    """

    __table_args__ = (
        UniqueConstraint(
            "payroll_run_id", "run_version", name="uq_payroll_run_snapshots_run_version"
        ),
    )

    payroll_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("payroll_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    finalized_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    finalized_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    payroll_run: Mapped["PayrollRun"] = relationship("PayrollRun", backref="snapshots")

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return (
            f"<PayrollRunSnapshot id={self.id!r} payroll_run_id={self.payroll_run_id!r} "
            f"run_version={self.run_version!r}>"
        )

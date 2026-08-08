"""Payroll service: automatic rules, manual adjustments, and monthly runs.

Three cooperating pieces:

* Automatic-rule configuration (:meth:`PayrollService.list_rules`/
  :meth:`~PayrollService.set_rule`) — per-company, per-fact toggles
  that decide whether an attendance fact (lateness, early leave,
  absence, missing punch, overtime) ever becomes a salary adjustment.
  Every rule starts disabled: attendance facts are always computed by
  :mod:`services.attendance_service` regardless of these rules, and
  this service never treats a fact as a deduction on its own.
* Manual adjustments (:meth:`~PayrollService.add_manual_adjustment`/
  :meth:`~PayrollService.cancel_adjustment`) — deductions/bonuses a
  manager enters directly for an employee, independent of any
  automatic rule.
* Monthly run computation (:meth:`~PayrollService.compute_payroll_run`/
  :meth:`~PayrollService.mark_reviewed`/
  :meth:`~PayrollService.finalize_payroll_run`/
  :meth:`~PayrollService.reopen_payroll_run`) — aggregates a month's
  attendance facts, regenerates that month's *automatic* adjustments
  from the currently enabled rules, sums them together with whatever
  manual adjustments exist, and produces one
  :class:`~models.payroll.PayrollRunLine` per employee. Finalizing
  writes an immutable :class:`~models.payroll.PayrollRunSnapshot`;
  once finalized, a run's period is locked (no adjustment can be
  added/cancelled, no recompute happens) until explicitly reopened.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from models.attendance import AttendanceRecord
from models.audit_log import AuditLog
from models.employee import Employee
from models.enums import (
    AttendanceDayStatus,
    AuditAction,
    PayrollAdjustmentSource,
    PayrollAdjustmentType,
    PayrollAutoRuleType,
    PayrollCalculationMethod,
    PayrollRunStatus,
)
from models.payroll import (
    PayrollAdjustment,
    PayrollAutomaticRule,
    PayrollRun,
    PayrollRunLine,
    PayrollRunSnapshot,
)
from repositories.attendance_repository import AttendanceRecordRepository
from repositories.audit_log_repository import AuditLogRepository
from repositories.company_settings_repository import CompanySettingsRepository
from repositories.employee_repository import EmployeeRepository
from repositories.payroll_repository import (
    PayrollAdjustmentRepository,
    PayrollAutomaticRuleRepository,
    PayrollRunLineRepository,
    PayrollRunRepository,
    PayrollRunSnapshotRepository,
)
from utils.validators import is_valid_salary

_CENTS = Decimal("0.01")

#: Statuses counted as "present" for PayrollRunLine.attendance_days --
#: the employee showed up, whether or not there was also a lateness/
#: early-leave fact recorded for the same day.
_PRESENT_STATUSES = frozenset(
    {AttendanceDayStatus.PRESENT, AttendanceDayStatus.LATE, AttendanceDayStatus.EARLY_LEAVE}
)
_MISSING_PUNCH_STATUSES = frozenset(
    {AttendanceDayStatus.MISSING_CHECK_IN, AttendanceDayStatus.MISSING_CHECK_OUT}
)

_RULE_REASON_TEMPLATES_AR = {
    PayrollAutoRuleType.LATE: "خصم تأخير {minutes} دقيقة",
    PayrollAutoRuleType.EARLY_LEAVE: "خصم انصراف مبكر {minutes} دقيقة",
    PayrollAutoRuleType.ABSENCE: "خصم غياب",
    PayrollAutoRuleType.MISSING_PUNCH: "خصم تسجيل حضور/انصراف مفقود",
    PayrollAutoRuleType.OVERTIME: "إضافة وقت إضافي {minutes} دقيقة",
}


def _round_currency(amount: Decimal) -> Decimal:
    """Round a currency amount to 2 decimal places, half-up."""
    return amount.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    """Return the first and last calendar day of ``year``-``month``."""
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


class PayrollValidationError(Exception):
    """Raised when payroll input (a rule, an adjustment) fails validation."""


class PayrollPeriodLockedError(Exception):
    """Raised when attempting to modify a pay period whose run is finalized."""


class PayrollRunStateError(Exception):
    """Raised when a payroll run operation is attempted from an invalid state."""


class PayrollService:
    """Payroll operations scoped to one company.

    Attributes:
        session: The active database session.
        company_id: The company this service operates within.
        actor_user_id: The user performing these operations, recorded
            on every audit log entry.
    """

    def __init__(
        self, session: Session, *, company_id: int, actor_user_id: int | None = None
    ) -> None:
        """Create a payroll service bound to one session and company.

        Args:
            session: The active database session.
            company_id: The company to operate within.
            actor_user_id: The acting user's id, for audit attribution.
        """
        self.session = session
        self.company_id = company_id
        self.actor_user_id = actor_user_id
        self.rule_repo = PayrollAutomaticRuleRepository(session, company_id=company_id)
        self.adjustment_repo = PayrollAdjustmentRepository(session, company_id=company_id)
        self.run_repo = PayrollRunRepository(session, company_id=company_id)
        self.line_repo = PayrollRunLineRepository(session, company_id=company_id)
        self.snapshot_repo = PayrollRunSnapshotRepository(session, company_id=company_id)
        self.employee_repo = EmployeeRepository(session, company_id=company_id)
        self.attendance_repo = AttendanceRecordRepository(session, company_id=company_id)
        self.settings_repo = CompanySettingsRepository(session, company_id=company_id)
        self.audit_repo = AuditLogRepository(session)

    # ------------------------------------------------------------------
    # Automatic rules
    # ------------------------------------------------------------------

    def list_rules(self) -> list[PayrollAutomaticRule]:
        """List this company's automatic-rule configuration, one row per rule type.

        Any rule type this company has never configured is created on
        the fly as disabled (``FIXED_AMOUNT`` of ``0``), so the caller
        (typically the payroll settings screen) always gets a complete,
        stable set of five rows to render toggles for.

        Returns:
            One :class:`~models.payroll.PayrollAutomaticRule` per
            :class:`~models.enums.PayrollAutoRuleType` member, in enum
            definition order.
        """
        existing = {rule.rule_type: rule for rule in self.rule_repo.list_all_rules()}
        for rule_type in PayrollAutoRuleType:
            if rule_type not in existing:
                rule = PayrollAutomaticRule(
                    company_id=self.company_id,
                    rule_type=rule_type,
                    enabled=False,
                    calculation_method=PayrollCalculationMethod.FIXED_AMOUNT,
                    value=Decimal("0"),
                    created_by_id=self.actor_user_id,
                )
                self.rule_repo.add(rule)
                existing[rule_type] = rule
        return [existing[rule_type] for rule_type in PayrollAutoRuleType]

    def set_rule(
        self,
        rule_type: PayrollAutoRuleType,
        *,
        enabled: bool,
        calculation_method: PayrollCalculationMethod,
        value: Decimal,
    ) -> PayrollAutomaticRule:
        """Enable/disable one automatic rule and configure its calculation.

        Args:
            rule_type: Which rule to configure.
            enabled: Whether this rule should produce salary
                adjustments going forward. Does not retroactively
                remove adjustments already generated for past periods.
            calculation_method: How :attr:`value` translates into a
                currency amount.
            value: The method's numeric parameter (see
                :class:`~models.payroll.PayrollAutomaticRule`'s own
                docstring).

        Returns:
            The updated (or newly created) rule row.

        Raises:
            PayrollValidationError: If ``value`` is negative or not a
                valid decimal amount.
        """
        if not is_valid_salary(value):
            raise PayrollValidationError("Rule value must be a non-negative amount.")

        rule = self.rule_repo.get_by_rule_type(rule_type)
        if rule is None:
            rule = PayrollAutomaticRule(
                company_id=self.company_id,
                rule_type=rule_type,
                enabled=enabled,
                calculation_method=calculation_method,
                value=_round_currency(Decimal(value)),
                created_by_id=self.actor_user_id,
            )
            self.rule_repo.add(rule)
        else:
            rule.enabled = enabled
            rule.calculation_method = calculation_method
            rule.value = _round_currency(Decimal(value))
            rule.updated_by_id = self.actor_user_id
            self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="PayrollAutomaticRule",
                entity_id=rule.id,
                description=(
                    f"{'Enabled' if enabled else 'Disabled'} automatic "
                    f"{rule_type.value} rule ({calculation_method.value}={value})."
                ),
            )
        )
        return rule

    # ------------------------------------------------------------------
    # Manual adjustments
    # ------------------------------------------------------------------

    def add_manual_adjustment(
        self,
        employee_id: int,
        *,
        adjustment_type: PayrollAdjustmentType,
        amount: Decimal,
        reason_text: str,
        adjustment_date: date,
        reason_category: str | None = None,
        notes: str | None = None,
    ) -> PayrollAdjustment:
        """Record a manual deduction or bonus for one employee.

        Args:
            employee_id: The affected employee; must belong to this
                company.
            adjustment_type: Deduction or bonus.
            amount: A positive currency amount.
            reason_text: Human-readable reason (required, e.g. the
                manager's own free text, or a category label the UI
                filled in).
            adjustment_date: The date this adjustment pertains to; also
                determines the pay period (``year``/``month``) it is
                filed under.
            reason_category: Optional stored category, expected to be
                a :class:`~models.enums.PayrollDeductionReason` or
                :class:`~models.enums.PayrollBonusReason` value
                depending on ``adjustment_type``.
            notes: Optional free-form notes.

        Returns:
            The newly created, persisted adjustment.

        Raises:
            PayrollValidationError: If the employee does not exist, the
                amount is not a positive decimal, or ``reason_text`` is
                empty.
            PayrollPeriodLockedError: If this pay period's run has
                already been finalized.
        """
        employee = self.employee_repo.get_by_id(employee_id)
        if employee is None:
            raise PayrollValidationError(f"Employee {employee_id!r} was not found.")
        if not is_valid_salary(amount) or Decimal(amount) <= 0:
            raise PayrollValidationError("Amount must be a positive value.")
        if not reason_text or not reason_text.strip():
            raise PayrollValidationError("A reason is required.")
        self._ensure_period_editable(adjustment_date.year, adjustment_date.month)

        adjustment = PayrollAdjustment(
            company_id=self.company_id,
            employee_id=employee_id,
            adjustment_type=adjustment_type,
            source=PayrollAdjustmentSource.MANUAL,
            year=adjustment_date.year,
            month=adjustment_date.month,
            work_date=adjustment_date,
            amount=_round_currency(Decimal(amount)),
            reason_category=reason_category,
            reason_text=reason_text.strip(),
            notes=notes,
            created_by_id=self.actor_user_id,
        )
        self.adjustment_repo.add(adjustment)

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.CREATE,
                entity_type="PayrollAdjustment",
                entity_id=adjustment.id,
                description=(
                    f"Added manual {adjustment_type.value} of {adjustment.amount} for "
                    f"employee {employee.full_name!r}: {reason_text.strip()}"
                ),
            )
        )
        return adjustment

    def cancel_adjustment(self, adjustment: PayrollAdjustment) -> PayrollAdjustment:
        """Cancel a deduction/bonus without destroying its audit trail.

        Works for both manual and automatic adjustments -- cancelling
        an automatic one is how a manager overrides a specific
        automatically-generated line without disabling the rule for
        everyone else.

        Args:
            adjustment: The adjustment to cancel; must belong to this
                company.

        Returns:
            The cancelled adjustment.

        Raises:
            PayrollValidationError: If the adjustment does not belong
                to this company.
            PayrollPeriodLockedError: If this pay period's run has
                already been finalized.
        """
        if adjustment.company_id != self.company_id:
            raise PayrollValidationError("This adjustment does not belong to the current company.")
        self._ensure_period_editable(adjustment.year, adjustment.month)

        adjustment.cancel()
        adjustment.updated_by_id = self.actor_user_id
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.DELETE,
                entity_type="PayrollAdjustment",
                entity_id=adjustment.id,
                description=(
                    f"Cancelled {adjustment.source.value} {adjustment.adjustment_type.value} "
                    f"adjustment of {adjustment.amount}."
                ),
            )
        )
        return adjustment

    def list_adjustments_for_employee_period(
        self, employee_id: int, *, year: int, month: int, include_cancelled: bool = False
    ) -> list[PayrollAdjustment]:
        """List one employee's deductions/bonuses for one pay period.

        Args:
            employee_id: The employee's id.
            year: The pay period's calendar year.
            month: The pay period's calendar month (1-12).
            include_cancelled: Whether to include cancelled entries.

        Returns:
            Matching adjustments, most recently created first.
        """
        return self.adjustment_repo.list_for_employee_period(
            employee_id, year=year, month=month, include_cancelled=include_cancelled
        )

    # ------------------------------------------------------------------
    # Monthly run computation
    # ------------------------------------------------------------------

    def compute_payroll_run(self, *, year: int, month: int) -> PayrollRun:
        """Compute (or recompute) this company's payroll run for one month.

        For every active employee: regenerates that month's *automatic*
        adjustments from the currently enabled rules (previous
        automatic adjustments for the same period are cancelled first,
        so this is safe to call repeatedly as attendance data or rule
        configuration changes), aggregates the month's attendance
        facts, sums automatic + manual deductions and bonuses, and
        writes/updates one :class:`~models.payroll.PayrollRunLine`.

        Recomputing a ``REVIEWED`` run resets it to ``DRAFT`` -- new
        figures need a fresh review. A ``FINALIZED`` run is never
        recomputed silently; see :meth:`reopen_payroll_run`.

        Args:
            year: The pay period's calendar year.
            month: The pay period's calendar month (1-12).

        Returns:
            The computed (created or updated) run, with its lines
            available via :meth:`list_lines`.

        Raises:
            PayrollRunStateError: If this period's run is already
                finalized.
        """
        run = self.run_repo.get_for_period(year=year, month=month)
        if run is not None and run.status is PayrollRunStatus.FINALIZED:
            raise PayrollRunStateError(
                "This payroll run is finalized; reopen it before recomputing."
            )
        if run is None:
            run = PayrollRun(
                company_id=self.company_id,
                year=year,
                month=month,
                status=PayrollRunStatus.DRAFT,
                created_by_id=self.actor_user_id,
            )
            self.run_repo.add(run)
        elif run.status is PayrollRunStatus.REVIEWED:
            run.status = PayrollRunStatus.DRAFT

        rules = {rule.rule_type: rule for rule in self.list_rules()}
        start_date, end_date = _month_bounds(year, month)
        now = _utc_now()

        for employee in self.employee_repo.list_active():
            self._regenerate_automatic_adjustments(
                employee, year=year, month=month, rules=rules, start_date=start_date, end_date=end_date
            )
            self.session.flush()

            records = self.attendance_repo.list_for_employee_between(
                employee.id, start_date, end_date
            )
            adjustments = self.adjustment_repo.list_for_employee_period(
                employee.id, year=year, month=month
            )

            base_salary = employee.salary if employee.salary is not None else Decimal("0")
            settings = self.settings_repo.get_or_create()
            working_days = max(int(settings.payroll_working_days_per_month), 1)

            automatic_deduction_total = _round_currency(
                sum(
                    (
                        a.amount
                        for a in adjustments
                        if a.adjustment_type is PayrollAdjustmentType.DEDUCTION
                        and a.source is PayrollAdjustmentSource.AUTOMATIC
                    ),
                    Decimal("0"),
                )
            )
            manual_deduction_total = _round_currency(
                sum(
                    (
                        a.amount
                        for a in adjustments
                        if a.adjustment_type is PayrollAdjustmentType.DEDUCTION
                        and a.source is PayrollAdjustmentSource.MANUAL
                    ),
                    Decimal("0"),
                )
            )
            bonus_total = _round_currency(
                sum(
                    (a.amount for a in adjustments if a.adjustment_type is PayrollAdjustmentType.BONUS),
                    Decimal("0"),
                )
            )
            net_salary = _round_currency(
                base_salary - automatic_deduction_total - manual_deduction_total + bonus_total
            )

            attendance_days = sum(1 for r in records if r.status in _PRESENT_STATUSES)
            absence_days = sum(1 for r in records if r.status is AttendanceDayStatus.ABSENT)
            missing_punch_days = sum(1 for r in records if r.status in _MISSING_PUNCH_STATUSES)
            late_minutes_total = sum(r.late_minutes for r in records)
            early_leave_minutes_total = sum(r.early_leave_minutes for r in records)
            overtime_minutes_total = sum(r.overtime_minutes for r in records)

            line = self.line_repo.get_for_run_and_employee(run.id, employee.id)
            if line is None:
                line = PayrollRunLine(
                    company_id=self.company_id,
                    payroll_run_id=run.id,
                    employee_id=employee.id,
                    base_salary=base_salary,
                    working_days_in_period=working_days,
                    attendance_days=attendance_days,
                    absence_days=absence_days,
                    missing_punch_days=missing_punch_days,
                    late_minutes_total=late_minutes_total,
                    early_leave_minutes_total=early_leave_minutes_total,
                    overtime_minutes_total=overtime_minutes_total,
                    automatic_deduction_total=automatic_deduction_total,
                    manual_deduction_total=manual_deduction_total,
                    bonus_total=bonus_total,
                    net_salary=net_salary,
                    computed_at=now,
                    created_by_id=self.actor_user_id,
                )
                self.line_repo.add(line)
                continue

            line.base_salary = base_salary
            line.working_days_in_period = working_days
            line.attendance_days = attendance_days
            line.absence_days = absence_days
            line.missing_punch_days = missing_punch_days
            line.late_minutes_total = late_minutes_total
            line.early_leave_minutes_total = early_leave_minutes_total
            line.overtime_minutes_total = overtime_minutes_total
            line.automatic_deduction_total = automatic_deduction_total
            line.manual_deduction_total = manual_deduction_total
            line.bonus_total = bonus_total
            line.net_salary = net_salary
            line.computed_at = now
            line.updated_by_id = self.actor_user_id

        self.session.flush()
        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="PayrollRun",
                entity_id=run.id,
                description=f"Computed payroll run for {year:04d}-{month:02d}.",
            )
        )
        return run

    def list_lines(self, payroll_run_id: int) -> list[PayrollRunLine]:
        """List every employee's computed line for a run.

        Args:
            payroll_run_id: The run's id.

        Returns:
            Lines ordered by employee id.
        """
        return self.line_repo.list_for_run(payroll_run_id)

    def get_run_for_period(self, *, year: int, month: int) -> PayrollRun | None:
        """Fetch this company's payroll run for one month, if it has been computed.

        Args:
            year: The pay period's calendar year.
            month: The pay period's calendar month (1-12).

        Returns:
            The matching run, or ``None``.
        """
        return self.run_repo.get_for_period(year=year, month=month)

    def list_recent_runs(self, *, limit: int = 24) -> list[PayrollRun]:
        """List this company's most recent payroll runs, newest first.

        Args:
            limit: Maximum number of runs to return.

        Returns:
            Matching runs.
        """
        return self.run_repo.list_recent(limit=limit)

    def mark_reviewed(self, run: PayrollRun) -> PayrollRun:
        """Move a payroll run from draft to reviewed.

        Args:
            run: The run to mark reviewed; must be in ``DRAFT`` status.

        Returns:
            The updated run.

        Raises:
            PayrollRunStateError: If ``run`` is not currently ``DRAFT``.
        """
        if run.status is not PayrollRunStatus.DRAFT:
            raise PayrollRunStateError("Only a draft payroll run can be marked reviewed.")
        run.status = PayrollRunStatus.REVIEWED
        run.updated_by_id = self.actor_user_id
        self.session.flush()
        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="PayrollRun",
                entity_id=run.id,
                description=f"Marked payroll run {run.year:04d}-{run.month:02d} as reviewed.",
            )
        )
        return run

    def finalize_payroll_run(self, run: PayrollRun) -> PayrollRun:
        """Finalize a payroll run, freezing an immutable snapshot of its figures.

        Recomputes one last time first, so the snapshot reflects the
        absolute latest attendance/adjustment data at the moment of
        finalization. After this call, the run's period is locked:
        no adjustment for it can be added or cancelled, and
        :meth:`compute_payroll_run` refuses to touch it, until
        :meth:`reopen_payroll_run` is called.

        Args:
            run: The run to finalize; must be ``DRAFT`` or ``REVIEWED``.

        Returns:
            The finalized run.

        Raises:
            PayrollRunStateError: If ``run`` is already finalized.
        """
        if run.status is PayrollRunStatus.FINALIZED:
            raise PayrollRunStateError("This payroll run is already finalized.")

        run = self.compute_payroll_run(year=run.year, month=run.month)

        now = _utc_now()
        run.status = PayrollRunStatus.FINALIZED
        run.finalized_at = now
        run.finalized_by_id = self.actor_user_id
        run.run_version += 1
        self.session.flush()

        lines = self.list_lines(run.id)
        snapshot_json = {
            "year": run.year,
            "month": run.month,
            "run_version": run.run_version,
            "finalized_at": now.isoformat(),
            "rules": [
                {
                    "rule_type": rule.rule_type.value,
                    "enabled": rule.enabled,
                    "calculation_method": rule.calculation_method.value,
                    "value": str(rule.value),
                }
                for rule in self.list_rules()
            ],
            "lines": [
                {
                    "employee_id": line.employee_id,
                    "base_salary": str(line.base_salary),
                    "working_days_in_period": line.working_days_in_period,
                    "attendance_days": line.attendance_days,
                    "absence_days": line.absence_days,
                    "missing_punch_days": line.missing_punch_days,
                    "late_minutes_total": line.late_minutes_total,
                    "early_leave_minutes_total": line.early_leave_minutes_total,
                    "overtime_minutes_total": line.overtime_minutes_total,
                    "automatic_deduction_total": str(line.automatic_deduction_total),
                    "manual_deduction_total": str(line.manual_deduction_total),
                    "bonus_total": str(line.bonus_total),
                    "net_salary": str(line.net_salary),
                }
                for line in lines
            ],
        }
        snapshot = PayrollRunSnapshot(
            company_id=self.company_id,
            payroll_run_id=run.id,
            run_version=run.run_version,
            snapshot_json=snapshot_json,
            finalized_at=now,
            finalized_by_id=self.actor_user_id,
            created_by_id=self.actor_user_id,
        )
        self.snapshot_repo.add(snapshot)

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="PayrollRun",
                entity_id=run.id,
                description=(
                    f"Finalized payroll run for {run.year:04d}-{run.month:02d} "
                    f"(version {run.run_version}, {len(lines)} employee(s))."
                ),
            )
        )
        return run

    def reopen_payroll_run(self, run: PayrollRun, *, reason: str | None = None) -> PayrollRun:
        """Reopen a finalized payroll run for correction.

        Does not touch any existing :class:`~models.payroll.PayrollRunSnapshot`
        -- every past finalization remains queryable exactly as it was
        approved. The run returns to ``DRAFT`` so its figures can be
        corrected and it must be explicitly re-finalized (producing a
        new, later-versioned snapshot) before it counts as approved
        again.

        Args:
            run: The run to reopen; must be ``FINALIZED``.
            reason: Optional free-form reason, recorded on the audit
                trail.

        Returns:
            The reopened run.

        Raises:
            PayrollRunStateError: If ``run`` is not currently finalized.
        """
        if run.status is not PayrollRunStatus.FINALIZED:
            raise PayrollRunStateError("Only a finalized payroll run can be reopened.")

        run.status = PayrollRunStatus.DRAFT
        run.reopened_at = _utc_now()
        run.reopened_by_id = self.actor_user_id
        run.updated_by_id = self.actor_user_id
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="PayrollRun",
                entity_id=run.id,
                description=(
                    f"Reopened finalized payroll run for {run.year:04d}-{run.month:02d}."
                    + (f" Reason: {reason}" if reason else "")
                ),
            )
        )
        return run

    def list_snapshots(self, payroll_run_id: int) -> list[PayrollRunSnapshot]:
        """List every historical finalize snapshot for a run, oldest first.

        Args:
            payroll_run_id: The run's id.

        Returns:
            Matching snapshots.
        """
        return self.snapshot_repo.list_for_run(payroll_run_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_period_editable(self, year: int, month: int) -> None:
        """Raise if this pay period's run is already finalized."""
        run = self.run_repo.get_for_period(year=year, month=month)
        if run is not None and run.status is PayrollRunStatus.FINALIZED:
            raise PayrollPeriodLockedError(
                f"The payroll run for {year:04d}-{month:02d} is finalized; "
                "reopen it before making changes."
            )

    def _rates_for(self, base_salary: Decimal) -> tuple[Decimal, Decimal]:
        """Derive (daily_rate, working_days) from this company's payroll settings."""
        settings = self.settings_repo.get_or_create()
        working_days = max(int(settings.payroll_working_days_per_month), 1)
        daily_rate = base_salary / Decimal(working_days)
        return daily_rate, Decimal(working_days)

    def _amount_for_rule(
        self, rule: PayrollAutomaticRule, *, minutes: int, daily_rate: Decimal
    ) -> Decimal:
        """Translate a rule's configured method/value into a currency amount."""
        method = rule.calculation_method
        if method is PayrollCalculationMethod.FIXED_AMOUNT:
            amount = rule.value
        elif method is PayrollCalculationMethod.PER_MINUTE:
            amount = rule.value * Decimal(minutes)
        elif method is PayrollCalculationMethod.PER_HOUR:
            amount = rule.value * Decimal(minutes) / Decimal(60)
        elif method is PayrollCalculationMethod.HALF_DAY:
            amount = daily_rate / 2
        elif method is PayrollCalculationMethod.FULL_DAY:
            amount = daily_rate
        elif method is PayrollCalculationMethod.PERCENTAGE_OF_DAILY_SALARY:
            amount = daily_rate * rule.value / Decimal(100)
        else:  # pragma: no cover - exhaustive over PayrollCalculationMethod
            amount = Decimal("0")
        return _round_currency(max(amount, Decimal("0")))

    def _regenerate_automatic_adjustments(
        self,
        employee: Employee,
        *,
        year: int,
        month: int,
        rules: dict[PayrollAutoRuleType, PayrollAutomaticRule],
        start_date: date,
        end_date: date,
    ) -> None:
        """Cancel this employee's previous automatic adjustments for the period and regenerate them.

        Safe to call repeatedly (each :meth:`compute_payroll_run` call
        does so for every employee): attendance data or rule
        configuration may have changed since the last computation, and
        the automatic adjustments must always reflect the current
        state, not accumulate stale duplicates.
        """
        existing_automatic = [
            adjustment
            for adjustment in self.adjustment_repo.list_for_employee_period(
                employee.id, year=year, month=month
            )
            if adjustment.source is PayrollAdjustmentSource.AUTOMATIC
        ]
        for adjustment in existing_automatic:
            adjustment.cancel()

        if employee.salary is None:
            return

        daily_rate, _ = self._rates_for(employee.salary)
        records = self.attendance_repo.list_for_employee_between(employee.id, start_date, end_date)

        for record in records:
            if record.status is AttendanceDayStatus.LATE and record.late_minutes > 0:
                self._apply_rule_if_enabled(
                    employee,
                    record,
                    rules.get(PayrollAutoRuleType.LATE),
                    minutes=record.late_minutes,
                    daily_rate=daily_rate,
                    adjustment_type=PayrollAdjustmentType.DEDUCTION,
                )
            if record.status is AttendanceDayStatus.EARLY_LEAVE and record.early_leave_minutes > 0:
                self._apply_rule_if_enabled(
                    employee,
                    record,
                    rules.get(PayrollAutoRuleType.EARLY_LEAVE),
                    minutes=record.early_leave_minutes,
                    daily_rate=daily_rate,
                    adjustment_type=PayrollAdjustmentType.DEDUCTION,
                )
            if record.status is AttendanceDayStatus.ABSENT:
                self._apply_rule_if_enabled(
                    employee,
                    record,
                    rules.get(PayrollAutoRuleType.ABSENCE),
                    minutes=0,
                    daily_rate=daily_rate,
                    adjustment_type=PayrollAdjustmentType.DEDUCTION,
                )
            if record.status in _MISSING_PUNCH_STATUSES:
                self._apply_rule_if_enabled(
                    employee,
                    record,
                    rules.get(PayrollAutoRuleType.MISSING_PUNCH),
                    minutes=0,
                    daily_rate=daily_rate,
                    adjustment_type=PayrollAdjustmentType.DEDUCTION,
                )
            if record.overtime_minutes > 0:
                self._apply_rule_if_enabled(
                    employee,
                    record,
                    rules.get(PayrollAutoRuleType.OVERTIME),
                    minutes=record.overtime_minutes,
                    daily_rate=daily_rate,
                    adjustment_type=PayrollAdjustmentType.BONUS,
                )

    def _apply_rule_if_enabled(
        self,
        employee: Employee,
        record: AttendanceRecord,
        rule: PayrollAutomaticRule | None,
        *,
        minutes: int,
        daily_rate: Decimal,
        adjustment_type: PayrollAdjustmentType,
    ) -> None:
        """Create one automatic adjustment for ``record`` if its rule is enabled and nonzero."""
        if rule is None or not rule.enabled:
            return
        amount = self._amount_for_rule(rule, minutes=minutes, daily_rate=daily_rate)
        if amount <= 0:
            return

        reason_text = _RULE_REASON_TEMPLATES_AR[rule.rule_type].format(minutes=minutes)
        adjustment = PayrollAdjustment(
            company_id=self.company_id,
            employee_id=employee.id,
            adjustment_type=adjustment_type,
            source=PayrollAdjustmentSource.AUTOMATIC,
            year=record.work_date.year,
            month=record.work_date.month,
            work_date=record.work_date,
            amount=amount,
            reason_category=rule.rule_type.value,
            reason_text=reason_text,
            source_attendance_record_id=record.id,
            calculation_detail={
                "minutes": minutes,
                "method": rule.calculation_method.value,
                "rate": str(rule.value),
                "amount": str(amount),
            },
        )
        self.adjustment_repo.add(adjustment)

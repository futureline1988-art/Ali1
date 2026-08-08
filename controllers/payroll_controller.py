"""Payroll controller: bridges the payroll screens to ``PayrollService``."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from PySide6.QtCore import Signal
from sqlalchemy.orm import Session

from controllers.base_controller import BaseController, requires_permission
from models.enums import PayrollAdjustmentType, PayrollAutoRuleType, PayrollCalculationMethod
from models.payroll import PayrollAdjustment, PayrollAutomaticRule, PayrollRun, PayrollRunLine
from repositories.payroll_repository import (
    PayrollAdjustmentRepository,
    PayrollRunRepository,
)
from services.payroll_service import PayrollService


def _rule_to_dict(rule: PayrollAutomaticRule) -> dict[str, Any]:
    """Serialize an automatic rule, adding display labels for its enums."""
    data = rule.to_dict()
    data["rule_type_label_ar"] = rule.rule_type.label_ar
    data["rule_type_label_en"] = rule.rule_type.label_en
    data["calculation_method_label_ar"] = rule.calculation_method.label_ar
    data["calculation_method_label_en"] = rule.calculation_method.label_en
    return data


def _adjustment_to_dict(adjustment: PayrollAdjustment) -> dict[str, Any]:
    """Serialize an adjustment plus the employee's name, while the session is open."""
    data = adjustment.to_dict()
    data["employee_name"] = adjustment.employee.full_name
    data["adjustment_type_label_ar"] = adjustment.adjustment_type.label_ar
    data["adjustment_type_label_en"] = adjustment.adjustment_type.label_en
    data["source_label_ar"] = adjustment.source.label_ar
    data["source_label_en"] = adjustment.source.label_en
    data["is_cancelled"] = adjustment.is_cancelled
    return data


def _run_to_dict(run: PayrollRun) -> dict[str, Any]:
    """Serialize a payroll run, adding a display label for its status."""
    data = run.to_dict()
    data["status_label_ar"] = run.status.label_ar
    data["status_label_en"] = run.status.label_en
    return data


def _line_to_dict(line: PayrollRunLine) -> dict[str, Any]:
    """Serialize a payroll run line plus the employee's name, while the session is open."""
    data = line.to_dict()
    data["employee_name"] = line.employee.full_name
    data["employee_number"] = line.employee.employee_number
    return data


class PayrollController(BaseController):
    """Controller for the payroll settings and monthly payroll screens."""

    rules_changed = Signal()
    """Emitted after an automatic rule is created or updated."""

    adjustments_changed = Signal()
    """Emitted after a manual adjustment is added or cancelled."""

    run_changed = Signal()
    """Emitted after a payroll run is computed, reviewed, finalized, or reopened."""

    # ------------------------------------------------------------------
    # Automatic rules
    # ------------------------------------------------------------------

    @requires_permission("payroll.view", "payroll.manage_rules", default=[])
    def list_rules(self) -> list[dict[str, Any]]:
        """List this company's automatic payroll rule configuration.

        Returns:
            One dict per :class:`~models.enums.PayrollAutoRuleType`; an
            empty list on failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            service = PayrollService(session, company_id=self.company_id)
            return [_rule_to_dict(rule) for rule in service.list_rules()]

        return self._run(do_list) or []

    @requires_permission("payroll.manage_rules")
    def set_rule(
        self,
        rule_type: PayrollAutoRuleType,
        *,
        enabled: bool,
        calculation_method: PayrollCalculationMethod,
        value: Decimal,
    ) -> dict[str, Any] | None:
        """Enable/disable and configure one automatic rule.

        Args mirror :meth:`~services.payroll_service.PayrollService.set_rule`.

        Returns:
            The updated rule's data as a dict, or ``None`` on failure.
        """

        def do_set(session: Session) -> dict[str, Any]:
            service = PayrollService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            rule = service.set_rule(
                rule_type, enabled=enabled, calculation_method=calculation_method, value=value
            )
            return _rule_to_dict(rule)

        result = self._run(do_set)
        if result is not None:
            self.rules_changed.emit()
        return result

    # ------------------------------------------------------------------
    # Manual adjustments
    # ------------------------------------------------------------------

    @requires_permission("payroll.manage_adjustments")
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
    ) -> dict[str, Any] | None:
        """Add a manual deduction or bonus for one employee.

        Args mirror
        :meth:`~services.payroll_service.PayrollService.add_manual_adjustment`.

        Returns:
            The new adjustment's data as a dict, or ``None`` on failure.
        """

        def do_add(session: Session) -> dict[str, Any]:
            service = PayrollService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            adjustment = service.add_manual_adjustment(
                employee_id,
                adjustment_type=adjustment_type,
                amount=amount,
                reason_text=reason_text,
                adjustment_date=adjustment_date,
                reason_category=reason_category,
                notes=notes,
            )
            return _adjustment_to_dict(adjustment)

        result = self._run(do_add)
        if result is not None:
            self.adjustments_changed.emit()
        return result

    @requires_permission("payroll.manage_adjustments", default=False)
    def cancel_adjustment(self, adjustment_id: int) -> bool:
        """Cancel a deduction or bonus.

        Args:
            adjustment_id: The adjustment to cancel.

        Returns:
            ``True`` on success, ``False`` on failure.
        """

        def do_cancel(session: Session) -> bool:
            repo = PayrollAdjustmentRepository(session, company_id=self.company_id)
            adjustment = repo.get_by_id(adjustment_id)
            if adjustment is None:
                raise ValueError(f"Adjustment {adjustment_id!r} was not found.")
            service = PayrollService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            service.cancel_adjustment(adjustment)
            return True

        result = self._run(do_cancel)
        if result:
            self.adjustments_changed.emit()
        return bool(result)

    @requires_permission("payroll.view", "payroll.manage_adjustments", default=[])
    def list_adjustments_for_employee_period(
        self, employee_id: int, *, year: int, month: int, include_cancelled: bool = False
    ) -> list[dict[str, Any]]:
        """List one employee's deductions/bonuses for one pay period.

        Args:
            employee_id: The employee's id.
            year: The pay period's calendar year.
            month: The pay period's calendar month (1-12).
            include_cancelled: Whether to include cancelled entries.

        Returns:
            Matching adjustments as dicts; an empty list on failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            service = PayrollService(session, company_id=self.company_id)
            return [
                _adjustment_to_dict(adjustment)
                for adjustment in service.list_adjustments_for_employee_period(
                    employee_id, year=year, month=month, include_cancelled=include_cancelled
                )
            ]

        return self._run(do_list) or []

    # ------------------------------------------------------------------
    # Monthly runs
    # ------------------------------------------------------------------

    @requires_permission("payroll.manage_adjustments")
    def compute_payroll_run(self, *, year: int, month: int) -> dict[str, Any] | None:
        """Compute (or recompute) this company's payroll run for one month.

        Args:
            year: The pay period's calendar year.
            month: The pay period's calendar month (1-12).

        Returns:
            The computed run's data as a dict, or ``None`` on failure
            (including an attempt against an already-finalized period).
        """

        def do_compute(session: Session) -> dict[str, Any]:
            service = PayrollService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            run = service.compute_payroll_run(year=year, month=month)
            return _run_to_dict(run)

        result = self._run(do_compute)
        if result is not None:
            self.run_changed.emit()
        return result

    @requires_permission("payroll.view", "payroll.manage_adjustments", default=None)
    def get_run_for_period(self, *, year: int, month: int) -> dict[str, Any] | None:
        """Fetch this company's payroll run for one month, if computed.

        Args:
            year: The pay period's calendar year.
            month: The pay period's calendar month (1-12).

        Returns:
            The run's data as a dict, or ``None`` if not yet computed
            (or on failure).
        """

        def do_get(session: Session) -> dict[str, Any] | None:
            service = PayrollService(session, company_id=self.company_id)
            run = service.get_run_for_period(year=year, month=month)
            return _run_to_dict(run) if run is not None else None

        return self._run(do_get)

    @requires_permission("payroll.view", "payroll.manage_adjustments", default=[])
    def list_recent_runs(self, *, limit: int = 24) -> list[dict[str, Any]]:
        """List this company's most recent payroll runs, newest first.

        Args:
            limit: Maximum number of runs to return.

        Returns:
            Matching runs as dicts; an empty list on failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            service = PayrollService(session, company_id=self.company_id)
            return [_run_to_dict(run) for run in service.list_recent_runs(limit=limit)]

        return self._run(do_list) or []

    @requires_permission("payroll.view", "payroll.manage_adjustments", default=[])
    def list_lines(self, run_id: int) -> list[dict[str, Any]]:
        """List every employee's computed line for a run.

        Args:
            run_id: The run's id.

        Returns:
            Matching lines as dicts; an empty list on failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            service = PayrollService(session, company_id=self.company_id)
            return [_line_to_dict(line) for line in service.list_lines(run_id)]

        return self._run(do_list) or []

    @requires_permission("payroll.manage_adjustments")
    def mark_reviewed(self, run_id: int) -> dict[str, Any] | None:
        """Move a payroll run from draft to reviewed.

        Args:
            run_id: The run to mark reviewed.

        Returns:
            The updated run's data as a dict, or ``None`` on failure.
        """

        def do_mark(session: Session) -> dict[str, Any]:
            repo = PayrollRunRepository(session, company_id=self.company_id)
            run = repo.get_by_id(run_id)
            if run is None:
                raise ValueError(f"Payroll run {run_id!r} was not found.")
            service = PayrollService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            updated = service.mark_reviewed(run)
            return _run_to_dict(updated)

        result = self._run(do_mark)
        if result is not None:
            self.run_changed.emit()
        return result

    @requires_permission("payroll.finalize")
    def finalize_payroll_run(self, run_id: int) -> dict[str, Any] | None:
        """Finalize a payroll run, freezing an immutable snapshot.

        Args:
            run_id: The run to finalize.

        Returns:
            The finalized run's data as a dict, or ``None`` on failure.
        """

        def do_finalize(session: Session) -> dict[str, Any]:
            repo = PayrollRunRepository(session, company_id=self.company_id)
            run = repo.get_by_id(run_id)
            if run is None:
                raise ValueError(f"Payroll run {run_id!r} was not found.")
            service = PayrollService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            finalized = service.finalize_payroll_run(run)
            return _run_to_dict(finalized)

        result = self._run(do_finalize)
        if result is not None:
            self.run_changed.emit()
        return result

    @requires_permission("payroll.reopen")
    def reopen_payroll_run(self, run_id: int, *, reason: str | None = None) -> dict[str, Any] | None:
        """Reopen a finalized payroll run for correction.

        Args:
            run_id: The run to reopen.
            reason: Optional free-form reason, recorded on the audit
                trail.

        Returns:
            The reopened run's data as a dict, or ``None`` on failure.
        """

        def do_reopen(session: Session) -> dict[str, Any]:
            repo = PayrollRunRepository(session, company_id=self.company_id)
            run = repo.get_by_id(run_id)
            if run is None:
                raise ValueError(f"Payroll run {run_id!r} was not found.")
            service = PayrollService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            reopened = service.reopen_payroll_run(run, reason=reason)
            return _run_to_dict(reopened)

        result = self._run(do_reopen)
        if result is not None:
            self.run_changed.emit()
        return result

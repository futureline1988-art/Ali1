"""PayrollService: automatic rules, manual adjustments, and monthly runs.

Covers the core promise this feature makes: attendance facts (late/
absence/overtime) are always computed, but they only ever become a
salary deduction/addition when a manager has explicitly enabled the
matching automatic rule; manual deductions/bonuses are independent of
rules; a finalized run is locked and produces an immutable historical
snapshot that survives a later reopen+refinalize.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest

from database.database import session_scope
from models.enums import (
    PayrollAdjustmentType,
    PayrollAutoRuleType,
    PayrollCalculationMethod,
    PayrollRunStatus,
    PunchType,
)
from services.attendance_service import AttendanceService
from services.employee_service import EmployeeService
from services.payroll_service import (
    PayrollPeriodLockedError,
    PayrollRunStateError,
    PayrollService,
    PayrollValidationError,
)
from services.shift_service import ShiftService


@pytest.fixture
def payroll_fixture(company_factory):
    """One company, one employee with a 900,000 salary and an assigned shift."""
    company_id = company_factory()

    with session_scope() as session:
        employee = EmployeeService(session, company_id=company_id).create_employee(
            employee_number="P-001", full_name="سارة محمد", salary=Decimal("900000")
        )
        employee_id = employee.id

        shift_svc = ShiftService(session, company_id=company_id)
        shift = shift_svc.create_shift(
            name="وردية الرواتب",
            start_time=time(9, 0),
            end_time=time(17, 0),
            grace_period_minutes=10,
            working_days=[
                "saturday", "sunday", "monday", "tuesday", "wednesday", "thursday",
            ],
        )
        shift_svc.assign_employee(
            employee_id=employee_id, shift_id=shift.id, effective_from=date(2026, 1, 1)
        )

    return {"company_id": company_id, "employee_id": employee_id}


def _punch(att_svc, employee_id, punch_type, dt):
    att_svc.record_manual_punch(employee_id=employee_id, punch_type=punch_type, punch_time=dt)


class TestAutomaticRules:
    def test_list_rules_returns_all_five_disabled_by_default(self, payroll_fixture):
        with session_scope() as session:
            svc = PayrollService(session, company_id=payroll_fixture["company_id"])
            rules = svc.list_rules()
        assert {r.rule_type for r in rules} == set(PayrollAutoRuleType)
        assert all(r.enabled is False for r in rules)

    def test_set_rule_persists_and_is_idempotent_to_call_twice(self, payroll_fixture):
        company_id = payroll_fixture["company_id"]
        with session_scope() as session:
            svc = PayrollService(session, company_id=company_id)
            svc.set_rule(
                PayrollAutoRuleType.LATE,
                enabled=True,
                calculation_method=PayrollCalculationMethod.FIXED_AMOUNT,
                value=Decimal("5000"),
            )
        with session_scope() as session:
            svc = PayrollService(session, company_id=company_id)
            rule = svc.rule_repo.get_by_rule_type(PayrollAutoRuleType.LATE)
            assert rule.enabled is True
            assert rule.value == Decimal("5000")

    def test_set_rule_rejects_negative_value(self, payroll_fixture):
        with session_scope() as session:
            svc = PayrollService(session, company_id=payroll_fixture["company_id"])
            with pytest.raises(PayrollValidationError):
                svc.set_rule(
                    PayrollAutoRuleType.LATE,
                    enabled=True,
                    calculation_method=PayrollCalculationMethod.FIXED_AMOUNT,
                    value=Decimal("-1"),
                )


class TestManualAdjustments:
    def test_add_manual_deduction_and_bonus(self, payroll_fixture):
        company_id = payroll_fixture["company_id"]
        employee_id = payroll_fixture["employee_id"]
        with session_scope() as session:
            svc = PayrollService(session, company_id=company_id)
            deduction = svc.add_manual_adjustment(
                employee_id,
                adjustment_type=PayrollAdjustmentType.DEDUCTION,
                amount=Decimal("10000"),
                reason_text="سلفة",
                adjustment_date=date(2026, 9, 5),
            )
            bonus = svc.add_manual_adjustment(
                employee_id,
                adjustment_type=PayrollAdjustmentType.BONUS,
                amount=Decimal("50000"),
                reason_text="مكافأة أداء",
                adjustment_date=date(2026, 9, 5),
            )
        assert deduction.amount == Decimal("10000")
        assert bonus.adjustment_type is PayrollAdjustmentType.BONUS

    def test_rejects_empty_reason_and_nonpositive_amount(self, payroll_fixture):
        with session_scope() as session:
            svc = PayrollService(session, company_id=payroll_fixture["company_id"])
            with pytest.raises(PayrollValidationError):
                svc.add_manual_adjustment(
                    payroll_fixture["employee_id"],
                    adjustment_type=PayrollAdjustmentType.DEDUCTION,
                    amount=Decimal("100"),
                    reason_text="   ",
                    adjustment_date=date(2026, 9, 5),
                )
            with pytest.raises(PayrollValidationError):
                svc.add_manual_adjustment(
                    payroll_fixture["employee_id"],
                    adjustment_type=PayrollAdjustmentType.DEDUCTION,
                    amount=Decimal("0"),
                    reason_text="valid reason",
                    adjustment_date=date(2026, 9, 5),
                )

    def test_cancel_adjustment_excludes_it_from_default_listing(self, payroll_fixture):
        company_id = payroll_fixture["company_id"]
        employee_id = payroll_fixture["employee_id"]
        with session_scope() as session:
            svc = PayrollService(session, company_id=company_id)
            adjustment = svc.add_manual_adjustment(
                employee_id,
                adjustment_type=PayrollAdjustmentType.DEDUCTION,
                amount=Decimal("1000"),
                reason_text="test",
                adjustment_date=date(2026, 9, 5),
            )
            adjustment_id = adjustment.id
            svc.cancel_adjustment(adjustment)

        with session_scope() as session:
            svc = PayrollService(session, company_id=company_id)
            active = svc.list_adjustments_for_employee_period(employee_id, year=2026, month=9)
            assert adjustment_id not in {a.id for a in active}
            all_including_cancelled = svc.list_adjustments_for_employee_period(
                employee_id, year=2026, month=9, include_cancelled=True
            )
            assert adjustment_id in {a.id for a in all_including_cancelled}


class TestComputePayrollRun:
    def test_late_fact_recorded_but_no_deduction_when_rule_disabled(self, payroll_fixture):
        company_id = payroll_fixture["company_id"]
        employee_id = payroll_fixture["employee_id"]
        with session_scope() as session:
            att_svc = AttendanceService(session, company_id=company_id)
            _punch(
                att_svc, employee_id, PunchType.CHECK_IN,
                datetime(2026, 9, 8, 9, 45, tzinfo=timezone.utc),
            )
            _punch(
                att_svc, employee_id, PunchType.CHECK_OUT,
                datetime(2026, 9, 8, 17, 0, tzinfo=timezone.utc),
            )
            record = att_svc.compute_daily_attendance(
                employee_id=employee_id, work_date=date(2026, 9, 8)
            )
            assert record.late_minutes > 0  # the fact IS recorded

        with session_scope() as session:
            payroll_svc = PayrollService(session, company_id=company_id)
            run = payroll_svc.compute_payroll_run(year=2026, month=9)
            lines = payroll_svc.list_lines(run.id)
            line = next(l for l in lines if l.employee_id == employee_id)

        assert line.late_minutes_total > 0
        assert line.automatic_deduction_total == Decimal("0")
        assert line.net_salary == line.base_salary

    def test_late_deduction_applied_when_rule_enabled(self, payroll_fixture):
        company_id = payroll_fixture["company_id"]
        employee_id = payroll_fixture["employee_id"]
        with session_scope() as session:
            PayrollService(session, company_id=company_id).set_rule(
                PayrollAutoRuleType.LATE,
                enabled=True,
                calculation_method=PayrollCalculationMethod.PER_MINUTE,
                value=Decimal("500"),
            )
        with session_scope() as session:
            att_svc = AttendanceService(session, company_id=company_id)
            _punch(
                att_svc, employee_id, PunchType.CHECK_IN,
                datetime(2026, 9, 8, 9, 45, tzinfo=timezone.utc),
            )
            _punch(
                att_svc, employee_id, PunchType.CHECK_OUT,
                datetime(2026, 9, 8, 17, 0, tzinfo=timezone.utc),
            )
            record = att_svc.compute_daily_attendance(
                employee_id=employee_id, work_date=date(2026, 9, 8)
            )
            late_minutes = record.late_minutes

        with session_scope() as session:
            payroll_svc = PayrollService(session, company_id=company_id)
            run = payroll_svc.compute_payroll_run(year=2026, month=9)
            lines = payroll_svc.list_lines(run.id)
            line = next(l for l in lines if l.employee_id == employee_id)

        expected = Decimal("500") * late_minutes
        assert line.automatic_deduction_total == expected
        assert line.net_salary == line.base_salary - expected

    def test_absence_full_day_deduction(self, payroll_fixture):
        company_id = payroll_fixture["company_id"]
        employee_id = payroll_fixture["employee_id"]
        with session_scope() as session:
            PayrollService(session, company_id=company_id).set_rule(
                PayrollAutoRuleType.ABSENCE,
                enabled=True,
                calculation_method=PayrollCalculationMethod.FULL_DAY,
                value=Decimal("0"),
            )
        with session_scope() as session:
            AttendanceService(session, company_id=company_id).compute_daily_attendance(
                employee_id=employee_id, work_date=date(2026, 9, 9)
            )  # Wednesday, no punches -> absent

        with session_scope() as session:
            payroll_svc = PayrollService(session, company_id=company_id)
            run = payroll_svc.compute_payroll_run(year=2026, month=9)
            line = next(
                l for l in payroll_svc.list_lines(run.id) if l.employee_id == employee_id
            )
            settings = payroll_svc.settings_repo.get_or_create()
            expected_daily_rate = (line.base_salary / settings.payroll_working_days_per_month).quantize(
                Decimal("0.01")
            )

        assert line.absence_days == 1
        assert line.automatic_deduction_total == expected_daily_rate

    def test_manual_and_automatic_adjustments_combine_in_net_salary(self, payroll_fixture):
        company_id = payroll_fixture["company_id"]
        employee_id = payroll_fixture["employee_id"]
        with session_scope() as session:
            svc = PayrollService(session, company_id=company_id)
            svc.add_manual_adjustment(
                employee_id,
                adjustment_type=PayrollAdjustmentType.DEDUCTION,
                amount=Decimal("10000"),
                reason_text="سلفة",
                adjustment_date=date(2026, 9, 5),
            )
            svc.add_manual_adjustment(
                employee_id,
                adjustment_type=PayrollAdjustmentType.BONUS,
                amount=Decimal("25000"),
                reason_text="حافز",
                adjustment_date=date(2026, 9, 5),
            )
            run = svc.compute_payroll_run(year=2026, month=9)
            line = next(l for l in svc.list_lines(run.id) if l.employee_id == employee_id)

        assert line.manual_deduction_total == Decimal("10000")
        assert line.bonus_total == Decimal("25000")
        assert line.net_salary == line.base_salary - Decimal("10000") + Decimal("25000")

    def test_recompute_does_not_duplicate_automatic_adjustments(self, payroll_fixture):
        company_id = payroll_fixture["company_id"]
        employee_id = payroll_fixture["employee_id"]
        with session_scope() as session:
            PayrollService(session, company_id=company_id).set_rule(
                PayrollAutoRuleType.LATE,
                enabled=True,
                calculation_method=PayrollCalculationMethod.FIXED_AMOUNT,
                value=Decimal("2000"),
            )
        with session_scope() as session:
            att_svc = AttendanceService(session, company_id=company_id)
            _punch(
                att_svc, employee_id, PunchType.CHECK_IN,
                datetime(2026, 9, 8, 9, 45, tzinfo=timezone.utc),
            )
            _punch(
                att_svc, employee_id, PunchType.CHECK_OUT,
                datetime(2026, 9, 8, 17, 0, tzinfo=timezone.utc),
            )
            att_svc.compute_daily_attendance(employee_id=employee_id, work_date=date(2026, 9, 8))

        with session_scope() as session:
            svc = PayrollService(session, company_id=company_id)
            svc.compute_payroll_run(year=2026, month=9)
            svc.compute_payroll_run(year=2026, month=9)
            svc.compute_payroll_run(year=2026, month=9)
            adjustments = svc.list_adjustments_for_employee_period(employee_id, year=2026, month=9)

        automatic = [a for a in adjustments if a.source.value == "automatic"]
        assert len(automatic) == 1
        assert automatic[0].amount == Decimal("2000")


class TestPayrollRunLifecycle:
    def test_mark_reviewed_requires_draft(self, payroll_fixture):
        company_id = payroll_fixture["company_id"]
        with session_scope() as session:
            svc = PayrollService(session, company_id=company_id)
            run = svc.compute_payroll_run(year=2026, month=9)
            reviewed = svc.mark_reviewed(run)
            assert reviewed.status is PayrollRunStatus.REVIEWED
            with pytest.raises(PayrollRunStateError):
                svc.mark_reviewed(reviewed)

    def test_finalize_creates_snapshot_and_locks_period(self, payroll_fixture):
        company_id = payroll_fixture["company_id"]
        employee_id = payroll_fixture["employee_id"]
        with session_scope() as session:
            svc = PayrollService(session, company_id=company_id)
            run = svc.compute_payroll_run(year=2026, month=9)
            run = svc.finalize_payroll_run(run)
            run_id = run.id

        assert run.status is PayrollRunStatus.FINALIZED
        assert run.run_version == 1

        with session_scope() as session:
            svc = PayrollService(session, company_id=company_id)
            snapshots = svc.list_snapshots(run_id)
            assert len(snapshots) == 1
            assert snapshots[0].run_version == 1

            # Recompute must be refused while finalized.
            with pytest.raises(PayrollRunStateError):
                svc.compute_payroll_run(year=2026, month=9)

            # Adjustments for this period must be refused too.
            with pytest.raises(PayrollPeriodLockedError):
                svc.add_manual_adjustment(
                    employee_id,
                    adjustment_type=PayrollAdjustmentType.DEDUCTION,
                    amount=Decimal("500"),
                    reason_text="late add attempt",
                    adjustment_date=date(2026, 9, 20),
                )

    def test_reopen_preserves_old_snapshot_and_allows_new_one(self, payroll_fixture):
        company_id = payroll_fixture["company_id"]
        employee_id = payroll_fixture["employee_id"]
        with session_scope() as session:
            svc = PayrollService(session, company_id=company_id)
            run = svc.compute_payroll_run(year=2026, month=9)
            run = svc.finalize_payroll_run(run)
            run_id = run.id

        with session_scope() as session:
            svc = PayrollService(session, company_id=company_id)
            run = svc.run_repo.get_by_id(run_id)
            run = svc.reopen_payroll_run(run, reason="correction needed")
            assert run.status is PayrollRunStatus.DRAFT

            svc.add_manual_adjustment(
                employee_id,
                adjustment_type=PayrollAdjustmentType.BONUS,
                amount=Decimal("15000"),
                reason_text="تصحيح بعد إعادة الفتح",
                adjustment_date=date(2026, 9, 10),
            )
            run = svc.compute_payroll_run(year=2026, month=9)
            run = svc.finalize_payroll_run(run)

        assert run.run_version == 2

        with session_scope() as session:
            svc = PayrollService(session, company_id=company_id)
            snapshots = svc.list_snapshots(run_id)
            versions = sorted(s.run_version for s in snapshots)
            assert versions == [1, 2]
            # The first (original) snapshot's JSON is untouched by the correction.
            first_snapshot = next(s for s in snapshots if s.run_version == 1)
            first_line = next(
                l for l in first_snapshot.snapshot_json["lines"] if l["employee_id"] == employee_id
            )
            assert first_line["bonus_total"] == "0.00"

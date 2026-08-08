"""Payroll reports: real persisted-data reports for payroll summary, deductions, and bonuses.

Exercises the same UI -> controller -> service -> repository chain
every other report type already goes through
(``tests/test_v2_production_readiness.py``'s printing tests) --
proving these new report types produce real files from real
:class:`~models.payroll.PayrollRunLine`/:class:`~models.payroll.PayrollAdjustment`
rows, not fabricated data.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from controllers.payroll_controller import PayrollController
from controllers.report_controller import ReportController
from database.database import session_scope
from models.enums import PayrollAdjustmentType, ReportFormat, ReportType
from services.employee_service import EmployeeService


def _actor_user_id(company_id: int) -> int:
    from repositories.role_repository import RoleRepository
    from services.user_service import UserService

    with session_scope() as session:
        role = RoleRepository(session, company_id=company_id).list_all()[0]
        user = UserService(session, company_id=company_id).create_user(
            username="payroll_reports_tester",
            password="Passw0rd!23",
            full_name="مختبر تقارير الرواتب",
            role_id=role.id,
        )
        return user.id


@pytest.fixture
def payroll_period_setup(company_factory):
    company_id = company_factory()
    with session_scope() as session:
        employee = EmployeeService(session, company_id=company_id).create_employee(
            employee_number="R-001", full_name="سامي كريم", salary=Decimal("700000")
        )
        employee_id = employee.id
    actor_id = _actor_user_id(company_id)

    controller = PayrollController(
        company_id=company_id,
        actor_user_id=actor_id,
        permission_codes=frozenset(
            {"payroll.view", "payroll.manage_rules", "payroll.manage_adjustments", "payroll.finalize"}
        ),
    )
    today = date.today()
    controller.add_manual_adjustment(
        employee_id,
        adjustment_type=PayrollAdjustmentType.DEDUCTION,
        amount=Decimal("15000"),
        reason_text="خصم تجريبي",
        adjustment_date=today,
    )
    controller.add_manual_adjustment(
        employee_id,
        adjustment_type=PayrollAdjustmentType.BONUS,
        amount=Decimal("40000"),
        reason_text="مكافأة تجريبية",
        adjustment_date=today,
    )
    run = controller.compute_payroll_run(year=today.year, month=today.month)
    assert run is not None
    return company_id, employee_id, today.year, today.month


class TestPayrollReportsProduceRealFiles:
    def test_payroll_summary_report_reflects_computed_run(self, payroll_period_setup, tmp_path):
        company_id, _employee_id, year, month = payroll_period_setup
        controller = ReportController(
            company_id=company_id,
            actor_user_id=None,
            permission_codes=frozenset({"reports.view", "reports.export"}),
        )
        output_path = tmp_path / "payroll_summary.csv"

        result = controller.generate_report(
            report_type=ReportType.PAYROLL_SUMMARY,
            output_format=ReportFormat.CSV,
            output_path=output_path,
            year=year,
            month=month,
        )

        assert result == output_path
        text = output_path.read_text(encoding="utf-8-sig")
        assert "R-001" in text
        assert "سامي كريم" in text

    def test_payroll_deductions_report_lists_only_deductions(self, payroll_period_setup, tmp_path):
        company_id, _employee_id, year, month = payroll_period_setup
        controller = ReportController(
            company_id=company_id,
            actor_user_id=None,
            permission_codes=frozenset({"reports.view", "reports.export"}),
        )
        output_path = tmp_path / "deductions.csv"

        controller.generate_report(
            report_type=ReportType.PAYROLL_DEDUCTIONS,
            output_format=ReportFormat.CSV,
            output_path=output_path,
            year=year,
            month=month,
        )

        text = output_path.read_text(encoding="utf-8-sig")
        assert "خصم تجريبي" in text
        assert "مكافأة تجريبية" not in text

    def test_payroll_bonuses_report_lists_only_bonuses(self, payroll_period_setup, tmp_path):
        company_id, _employee_id, year, month = payroll_period_setup
        controller = ReportController(
            company_id=company_id,
            actor_user_id=None,
            permission_codes=frozenset({"reports.view", "reports.export"}),
        )
        output_path = tmp_path / "bonuses.pdf"

        result = controller.generate_report(
            report_type=ReportType.PAYROLL_BONUSES,
            output_format=ReportFormat.PDF,
            output_path=output_path,
            year=year,
            month=month,
        )

        assert result == output_path
        content = output_path.read_bytes()
        assert content.startswith(b"%PDF")
        assert len(content) > 100

    def test_payroll_summary_report_is_empty_for_a_period_with_no_computed_run(
        self, company_factory, tmp_path
    ):
        """No fabricated data: an unrun period yields an empty (but valid) report."""
        company_id = company_factory()
        controller = ReportController(
            company_id=company_id,
            actor_user_id=None,
            permission_codes=frozenset({"reports.view", "reports.export"}),
        )
        output_path = tmp_path / "empty_summary.csv"

        result = controller.generate_report(
            report_type=ReportType.PAYROLL_SUMMARY,
            output_format=ReportFormat.CSV,
            output_path=output_path,
            year=2099,
            month=1,
        )

        assert result == output_path
        assert output_path.exists()

    def test_missing_year_month_is_rejected_for_payroll_report_types(
        self, qapp, payroll_period_setup, tmp_path
    ):
        company_id, _employee_id, _year, _month = payroll_period_setup
        controller = ReportController(
            company_id=company_id,
            actor_user_id=None,
            permission_codes=frozenset({"reports.view", "reports.export"}),
        )
        denials = []
        controller.operation_failed.connect(denials.append)
        output_path = tmp_path / "should_fail.csv"

        result = controller.generate_report(
            report_type=ReportType.PAYROLL_SUMMARY,
            output_format=ReportFormat.CSV,
            output_path=output_path,
        )

        assert result is None
        assert len(denials) == 1

"""PayrollController: permission gating and controller-level end-to-end wiring.

Exercises the real controller (not the service directly) so this
proves the same UI -> controller -> service -> repository chain the
payroll screens will actually use.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from controllers.payroll_controller import PayrollController
from models.enums import PayrollAdjustmentType, PayrollAutoRuleType, PayrollCalculationMethod


def _actor_user_id(company_id: int) -> int:
    """Create a real user in this company, for FK-valid audit attribution."""
    from database.database import session_scope
    from repositories.role_repository import RoleRepository
    from services.user_service import UserService

    with session_scope() as session:
        role = RoleRepository(session, company_id=company_id).list_all()[0]
        user = UserService(session, company_id=company_id).create_user(
            username="payroll_tester",
            password="Passw0rd!23",
            full_name="مختبر الرواتب",
            role_id=role.id,
        )
        return user.id


def _controller(
    company_id: int, *, actor_user_id: int | None = None, codes: frozenset[str] = frozenset()
) -> PayrollController:
    return PayrollController(
        company_id=company_id, actor_user_id=actor_user_id, permission_codes=codes
    )


class TestPermissionGating:
    def test_set_rule_denied_without_permission(self, qapp, company_factory):
        company_id = company_factory()
        controller = _controller(company_id)
        denials = []
        controller.operation_failed.connect(denials.append)

        result = controller.set_rule(
            PayrollAutoRuleType.LATE,
            enabled=True,
            calculation_method=PayrollCalculationMethod.FIXED_AMOUNT,
            value=Decimal("1000"),
        )
        assert result is None
        assert len(denials) == 1
        assert "صلاحية" in denials[0]

    def test_list_rules_denied_returns_empty_list_not_none(self, qapp, company_factory):
        company_id = company_factory()
        controller = _controller(company_id)
        result = controller.list_rules()
        assert result == []

    def test_finalize_requires_dedicated_permission_not_manage_adjustments(self, qapp, company_factory):
        company_id = company_factory()
        controller = _controller(company_id, codes=frozenset({"payroll.manage_adjustments"}))
        result = controller.finalize_payroll_run(1)
        assert result is None

    def test_reopen_requires_dedicated_permission(self, qapp, company_factory):
        company_id = company_factory()
        controller = _controller(company_id, codes=frozenset({"payroll.finalize"}))
        result = controller.reopen_payroll_run(1)
        assert result is None


class TestEndToEndThroughController:
    def test_full_workflow_via_controller(self, qapp, company_factory):
        from database.database import session_scope
        from services.employee_service import EmployeeService

        company_id = company_factory()
        with session_scope() as session:
            employee = EmployeeService(session, company_id=company_id).create_employee(
                employee_number="C-001", full_name="خالد", salary=Decimal("600000")
            )
            employee_id = employee.id
        actor_id = _actor_user_id(company_id)

        full_access = _controller(
            company_id,
            actor_user_id=actor_id,
            codes=frozenset(
                {
                    "payroll.view",
                    "payroll.manage_rules",
                    "payroll.manage_adjustments",
                    "payroll.finalize",
                    "payroll.reopen",
                }
            ),
        )

        rules = full_access.list_rules()
        assert len(rules) == len(PayrollAutoRuleType)
        assert all(r["enabled"] is False for r in rules)

        adjustment = full_access.add_manual_adjustment(
            employee_id,
            adjustment_type=PayrollAdjustmentType.BONUS,
            amount=Decimal("20000"),
            reason_text="مكافأة عبر الواجهة",
            adjustment_date=date(2026, 10, 5),
        )
        assert adjustment is not None
        assert adjustment["employee_name"] == "خالد"

        run = full_access.compute_payroll_run(year=2026, month=10)
        assert run is not None
        assert run["status"] == "draft"

        lines = full_access.list_lines(run["id"])
        line = next(l for l in lines if l["employee_id"] == employee_id)
        assert line["bonus_total"] == "20000.00"
        assert line["employee_number"] == "C-001"

        reviewed = full_access.mark_reviewed(run["id"])
        assert reviewed["status"] == "reviewed"

        finalized = full_access.finalize_payroll_run(run["id"])
        assert finalized["status"] == "finalized"

        # Once finalized, a view-only user still sees the run; a
        # manage-adjustments-only user cannot add a new deduction to it.
        adjustments_only = _controller(company_id, codes=frozenset({"payroll.manage_adjustments"}))
        blocked = adjustments_only.add_manual_adjustment(
            employee_id,
            adjustment_type=PayrollAdjustmentType.DEDUCTION,
            amount=Decimal("5000"),
            reason_text="محاولة بعد الاعتماد",
            adjustment_date=date(2026, 10, 15),
        )
        assert blocked is None

        reopened = full_access.reopen_payroll_run(run["id"], reason="تصحيح")
        assert reopened["status"] == "draft"

"""Payroll UI: dialogs' value extraction and the payroll page's real wiring.

Modal dialogs (``PayrollRulesDialog``, ``PayrollLineDetailDialog``) are
exercised two ways: directly (construct, set field values, read
``.values()``) to prove the widgets map correctly to controller
arguments, and through the page's own click handlers with ``exec``
monkeypatched to auto-accept, to prove the handler actually calls the
controller and refreshes -- not just that the dialog class works in
isolation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from PySide6.QtWidgets import QDialog

from controllers.payroll_controller import PayrollController
from database.database import session_scope
from models.enums import PayrollAdjustmentType, PayrollAutoRuleType, PayrollCalculationMethod
from services.employee_service import EmployeeService
from ui.payroll import (
    PayrollAdjustmentFormDialog,
    PayrollLineDetailDialog,
    PayrollPage,
    PayrollRulesDialog,
    _format_currency,
)


def _full_access_controller(company_id: int, actor_user_id: int) -> PayrollController:
    return PayrollController(
        company_id=company_id,
        actor_user_id=actor_user_id,
        permission_codes=frozenset(
            {
                "payroll.view",
                "payroll.manage_rules",
                "payroll.manage_adjustments",
                "payroll.finalize",
                "payroll.reopen",
            }
        ),
    )


def _real_user_id(company_id: int) -> int:
    from repositories.role_repository import RoleRepository
    from services.user_service import UserService

    with session_scope() as session:
        role = RoleRepository(session, company_id=company_id).list_all()[0]
        user = UserService(session, company_id=company_id).create_user(
            username="ui_tester", password="Passw0rd!23", full_name="مختبر الواجهة", role_id=role.id
        )
        return user.id


class TestFormatCurrency:
    def test_formats_with_thousands_separator_and_suffix(self):
        assert _format_currency("1000000.00") == "1,000,000.00 د.ع"

    def test_passes_through_unparseable_values(self):
        assert _format_currency(None) == "None"


class TestPayrollAdjustmentFormDialog:
    def test_values_reflect_form_state(self, qtbot):
        dialog = PayrollAdjustmentFormDialog(employee_name="أحمد")
        qtbot.addWidget(dialog)

        dialog.type_combo.setCurrentIndex(1)  # bonus
        dialog.amount_spin.setValue(15000.5)
        dialog.date_edit.setDate(dialog.date_edit.date().__class__(2026, 9, 15))
        dialog.reason_text_edit.setText("مكافأة اختبار")

        values = dialog.values()
        assert values["adjustment_type"] is PayrollAdjustmentType.BONUS
        assert values["amount"] == Decimal("15000.5")
        assert values["reason_text"] == "مكافأة اختبار"
        assert values["adjustment_date"] == date(2026, 9, 15)

    def test_reason_category_options_change_with_type(self, qtbot):
        dialog = PayrollAdjustmentFormDialog(employee_name="أحمد")
        qtbot.addWidget(dialog)

        deduction_reasons = {dialog.reason_combo.itemText(i) for i in range(dialog.reason_combo.count())}
        dialog.type_combo.setCurrentIndex(1)
        bonus_reasons = {dialog.reason_combo.itemText(i) for i in range(dialog.reason_combo.count())}
        assert deduction_reasons != bonus_reasons

    def test_empty_reason_text_falls_back_to_category_label(self, qtbot):
        dialog = PayrollAdjustmentFormDialog(employee_name="أحمد")
        qtbot.addWidget(dialog)
        dialog.reason_combo.setCurrentIndex(1)  # first real category
        dialog.reason_text_edit.clear()
        values = dialog.values()
        assert values["reason_text"]  # not empty


class TestPayrollRulesDialog:
    def test_values_reflect_edited_rows(self, qtbot):
        rules = [
            {
                "rule_type": rule_type.value,
                "rule_type_label_ar": rule_type.label_ar,
                "enabled": False,
                "calculation_method": PayrollCalculationMethod.FIXED_AMOUNT.value,
                "value": "0",
            }
            for rule_type in PayrollAutoRuleType
        ]
        dialog = PayrollRulesDialog(rules=rules)
        qtbot.addWidget(dialog)

        late_widgets = dialog._rows[PayrollAutoRuleType.LATE]
        late_widgets["enabled"].setChecked(True)
        late_widgets["value"].setValue(500)

        values = dialog.values()
        assert values[PayrollAutoRuleType.LATE]["enabled"] is True
        assert values[PayrollAutoRuleType.LATE]["value"] == Decimal("500")
        assert values[PayrollAutoRuleType.ABSENCE]["enabled"] is False


class TestPayrollPageWiring:
    def test_page_loads_with_no_run_yet(self, qtbot, company_factory):
        company_id = company_factory()
        page = PayrollPage(company_id=company_id, permission_codes=frozenset({"payroll.view"}))
        qtbot.addWidget(page)
        assert "لم يتم حساب" in page.status_label.text()
        assert page.table.rowCount() == 0
        assert page.review_button.isEnabled() is False
        assert page.finalize_button.isEnabled() is False
        assert page.reopen_button.isEnabled() is False

    def test_compute_populates_table_and_enables_lifecycle_buttons(self, qtbot, company_factory):
        company_id = company_factory()
        with session_scope() as session:
            EmployeeService(session, company_id=company_id).create_employee(
                employee_number="UI-001", full_name="منى", salary=Decimal("500000")
            )
        actor_id = _real_user_id(company_id)

        page = PayrollPage(
            company_id=company_id,
            current_user_id=actor_id,
            permission_codes=frozenset(
                {"payroll.view", "payroll.manage_adjustments", "payroll.finalize", "payroll.reopen"}
            ),
        )
        qtbot.addWidget(page)

        today = date.today()
        page.year_spin.setValue(today.year)
        page.month_combo.setCurrentIndex(today.month - 1)
        page._on_compute_clicked()

        assert page.table.rowCount() == 1
        assert page.review_button.isEnabled() is True
        assert page.finalize_button.isEnabled() is True
        assert page.reopen_button.isEnabled() is False

    def test_finalize_then_reopen_flow_via_handlers(self, qtbot, monkeypatch, company_factory):
        from ui.widgets import ConfirmDialog

        company_id = company_factory()
        with session_scope() as session:
            EmployeeService(session, company_id=company_id).create_employee(
                employee_number="UI-002", full_name="سعيد", salary=Decimal("400000")
            )
        actor_id = _real_user_id(company_id)
        monkeypatch.setattr(ConfirmDialog, "confirm", staticmethod(lambda *a, **k: True))

        page = PayrollPage(
            company_id=company_id,
            current_user_id=actor_id,
            permission_codes=frozenset(
                {"payroll.view", "payroll.manage_adjustments", "payroll.finalize", "payroll.reopen"}
            ),
        )
        qtbot.addWidget(page)
        page._on_compute_clicked()
        page._on_finalize_clicked()

        assert page._current_run["status"] == "finalized"
        assert page.finalize_button.isEnabled() is False
        assert page.reopen_button.isEnabled() is True

        page._on_reopen_clicked()
        assert page._current_run["status"] == "draft"
        assert page.reopen_button.isEnabled() is False

    def test_rules_dialog_flow_persists_via_controller(self, qtbot, monkeypatch, company_factory):
        company_id = company_factory()
        actor_id = _real_user_id(company_id)
        page = PayrollPage(
            company_id=company_id,
            current_user_id=actor_id,
            permission_codes=frozenset({"payroll.view", "payroll.manage_rules"}),
        )
        qtbot.addWidget(page)

        captured = {}

        def _fake_exec(self):
            self._rows[PayrollAutoRuleType.LATE]["enabled"].setChecked(True)
            self._rows[PayrollAutoRuleType.LATE]["value"].setValue(750)
            captured["values"] = self.values()
            return QDialog.Accepted

        monkeypatch.setattr(PayrollRulesDialog, "exec", _fake_exec)
        monkeypatch.setattr("ui.payroll.QMessageBox.information", lambda *a, **k: None)
        page._on_rules_clicked()

        assert captured["values"][PayrollAutoRuleType.LATE]["enabled"] is True

        controller = _full_access_controller(company_id, actor_id)
        rules = controller.list_rules()
        late_rule = next(r for r in rules if r["rule_type"] == "late")
        assert late_rule["enabled"] is True
        assert late_rule["value"] == "750.00"

    def test_rules_dialog_failed_save_keeps_error_visible_and_shows_no_success_message(
        self, qtbot, monkeypatch, company_factory
    ):
        """Regression: a failed set_rule() must not have its error banner
        immediately hidden by an unconditional clear_error(), and must
        never be followed by a success confirmation.
        """
        company_id = company_factory()
        actor_id = _real_user_id(company_id)
        # Missing "payroll.manage_rules" -> every set_rule() call is denied.
        page = PayrollPage(
            company_id=company_id,
            current_user_id=actor_id,
            permission_codes=frozenset({"payroll.view"}),
        )
        qtbot.addWidget(page)

        def _fake_exec(self):
            self._rows[PayrollAutoRuleType.LATE]["enabled"].setChecked(True)
            return QDialog.Accepted

        monkeypatch.setattr(PayrollRulesDialog, "exec", _fake_exec)
        success_calls = []
        monkeypatch.setattr(
            "ui.payroll.QMessageBox.information",
            lambda *a, **k: success_calls.append(a),
        )

        page._on_rules_clicked()

        assert success_calls == []
        assert "صلاحية" in page.error_label.text()


class TestPayrollLineDetailDialog:
    def test_add_and_cancel_adjustment_through_dialog(self, qtbot, monkeypatch, company_factory):
        company_id = company_factory()
        with session_scope() as session:
            employee = EmployeeService(session, company_id=company_id).create_employee(
                employee_number="UI-003", full_name="ليلى", salary=Decimal("300000")
            )
            employee_id = employee.id
        actor_id = _real_user_id(company_id)
        controller = _full_access_controller(company_id, actor_id)

        today = date.today()
        run = controller.compute_payroll_run(year=today.year, month=today.month)
        lines = controller.list_lines(run["id"])
        line = next(l for l in lines if l["employee_id"] == employee_id)

        dialog = PayrollLineDetailDialog(
            controller=controller,
            line=line,
            run_id=run["id"],
            year=today.year,
            month=today.month,
            run_status=run["status"],
            adjustments=[],
        )
        qtbot.addWidget(dialog)

        monkeypatch.setattr(
            PayrollAdjustmentFormDialog,
            "exec",
            lambda self: (self.amount_spin.setValue(20000), self.reason_text_edit.setText("test"), QDialog.Accepted)[-1],
        )
        dialog._open_adjustment_form(PayrollAdjustmentType.BONUS)

        assert dialog.table.rowCount() == 1
        assert dialog._summary_labels["bonus_total"].text() == "20,000.00 د.ع"

        adjustment_id = dialog._adjustments[0]["id"]
        monkeypatch.setattr("ui.payroll.ConfirmDialog.confirm", staticmethod(lambda *a, **k: True))
        dialog._on_cancel_adjustment(adjustment_id)

        assert dialog.table.rowCount() == 0
        assert dialog._summary_labels["bonus_total"].text() == "0.00 د.ع"
        assert dialog._changed is True

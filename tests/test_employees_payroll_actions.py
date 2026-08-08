"""Employees screen: "إضافة خصم"/"إضافة مكافأة" wiring to real payroll persistence.

Proves these toolbar actions are not decorative: selecting an employee,
clicking the button, filling the shared
:class:`~ui.payroll.PayrollAdjustmentFormDialog`, and accepting it must
create a real, persisted :class:`~models.payroll.PayrollAdjustment` --
verified by reading it back through :class:`~controllers.payroll_controller.PayrollController`,
not by inspecting the dialog alone.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import QDialog

from controllers.payroll_controller import PayrollController
from database.database import session_scope
from models.enums import PayrollAdjustmentType
from services.employee_service import EmployeeService
from ui.employees import EmployeesPage
from ui.payroll import PayrollAdjustmentFormDialog


def _actor_user_id(company_id: int) -> int:
    from repositories.role_repository import RoleRepository
    from services.user_service import UserService

    with session_scope() as session:
        role = RoleRepository(session, company_id=company_id).list_all()[0]
        user = UserService(session, company_id=company_id).create_user(
            username="employees_payroll_tester",
            password="Passw0rd!23",
            full_name="مختبر رواتب الموظفين",
            role_id=role.id,
        )
        return user.id


def _full_permissions() -> frozenset[str]:
    return frozenset(
        {
            "employees.view",
            "employees.manage",
            "devices.view",
            "devices.manage",
            "payroll.view",
            "payroll.manage_adjustments",
        }
    )


class TestEmployeeDeductionAndBonusActions:
    def test_buttons_disabled_without_selection(self, qtbot, company_factory):
        company_id = company_factory()
        page = EmployeesPage(company_id=company_id, permission_codes=_full_permissions())
        qtbot.addWidget(page)

        assert page.add_deduction_button.isEnabled() is False
        assert page.add_bonus_button.isEnabled() is False

    def test_add_deduction_through_dialog_persists_via_controller(
        self, qtbot, monkeypatch, company_factory
    ):
        company_id = company_factory()
        with session_scope() as session:
            employee = EmployeeService(session, company_id=company_id).create_employee(
                employee_number="EMP-D01", full_name="سارة"
            )
            employee_id = employee.id
        actor_id = _actor_user_id(company_id)

        page = EmployeesPage(
            company_id=company_id, current_user_id=actor_id, permission_codes=_full_permissions()
        )
        qtbot.addWidget(page)
        page.refresh()
        page.table.selectRow(0)

        def _fake_exec(self):
            self.amount_spin.setValue(15000)
            self.reason_text_edit.setText("خصم تأخير")
            return QDialog.Accepted

        monkeypatch.setattr(PayrollAdjustmentFormDialog, "exec", _fake_exec)
        monkeypatch.setattr("ui.employees.QMessageBox.information", lambda *a, **k: None)
        page._on_add_deduction_clicked()

        controller = PayrollController(
            company_id=company_id,
            actor_user_id=actor_id,
            permission_codes=frozenset({"payroll.view", "payroll.manage_adjustments"}),
        )
        today = date.today()
        adjustments = controller.list_adjustments_for_employee_period(
            employee_id, year=today.year, month=today.month
        )
        assert len(adjustments) == 1
        assert adjustments[0]["adjustment_type"] == PayrollAdjustmentType.DEDUCTION.value
        assert adjustments[0]["amount"] == "15000.00"
        assert adjustments[0]["reason_text"] == "خصم تأخير"

    def test_add_bonus_through_dialog_persists_via_controller(
        self, qtbot, monkeypatch, company_factory
    ):
        company_id = company_factory()
        with session_scope() as session:
            employee = EmployeeService(session, company_id=company_id).create_employee(
                employee_number="EMP-B01", full_name="علي"
            )
            employee_id = employee.id
        actor_id = _actor_user_id(company_id)

        page = EmployeesPage(
            company_id=company_id, current_user_id=actor_id, permission_codes=_full_permissions()
        )
        qtbot.addWidget(page)
        page.refresh()
        page.table.selectRow(0)

        def _fake_exec(self):
            self.type_combo.setCurrentIndex(1)  # bonus
            self.amount_spin.setValue(30000)
            self.reason_text_edit.setText("مكافأة أداء")
            return QDialog.Accepted

        monkeypatch.setattr(PayrollAdjustmentFormDialog, "exec", _fake_exec)
        monkeypatch.setattr("ui.employees.QMessageBox.information", lambda *a, **k: None)
        page._on_add_bonus_clicked()

        controller = PayrollController(
            company_id=company_id,
            actor_user_id=actor_id,
            permission_codes=frozenset({"payroll.view", "payroll.manage_adjustments"}),
        )
        today = date.today()
        adjustments = controller.list_adjustments_for_employee_period(
            employee_id, year=today.year, month=today.month
        )
        assert len(adjustments) == 1
        assert adjustments[0]["adjustment_type"] == PayrollAdjustmentType.BONUS.value
        assert adjustments[0]["amount"] == "30000.00"

    def test_add_deduction_denied_without_permission_shows_arabic_error(
        self, qtbot, monkeypatch, company_factory
    ):
        company_id = company_factory()
        with session_scope() as session:
            EmployeeService(session, company_id=company_id).create_employee(
                employee_number="EMP-D02", full_name="محمد"
            )

        page = EmployeesPage(
            company_id=company_id,
            current_user_id=None,
            permission_codes=frozenset({"employees.view", "employees.manage"}),
        )
        qtbot.addWidget(page)
        page.refresh()
        page.table.selectRow(0)

        def _fake_exec(self):
            self.amount_spin.setValue(5000)
            self.reason_text_edit.setText("خصم")
            return QDialog.Accepted

        monkeypatch.setattr(PayrollAdjustmentFormDialog, "exec", _fake_exec)
        page._on_add_deduction_clicked()

        assert "صلاحية" in page.error_label.text()

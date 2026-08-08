"""Reports screen: payroll report type wiring (year/month picker vs. date range).

Proves selecting a payroll report type switches the picker that's
actually used and that clicking "توليد التقرير" reaches the real
``ReportController.generate_report`` with ``year``/``month`` (not a
date range) for those types.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from PySide6.QtWidgets import QFileDialog

from database.database import session_scope
from models.enums import PayrollAdjustmentType, ReportType
from services.employee_service import EmployeeService
from ui.reports import ReportsPage


def _actor_user_id(company_id: int) -> int:
    from repositories.role_repository import RoleRepository
    from services.user_service import UserService

    with session_scope() as session:
        role = RoleRepository(session, company_id=company_id).list_all()[0]
        user = UserService(session, company_id=company_id).create_user(
            username="reports_ui_tester",
            password="Passw0rd!23",
            full_name="مختبر شاشة التقارير",
            role_id=role.id,
        )
        return user.id


def _full_permissions() -> frozenset[str]:
    return frozenset({"reports.view", "reports.export", "employees.view", "departments.view"})


class TestReportTypeTogglesPickers:
    def test_payroll_type_enables_period_picker_and_disables_date_range(
        self, qtbot, company_factory
    ):
        company_id = company_factory()
        page = ReportsPage(company_id=company_id, permission_codes=_full_permissions())
        qtbot.addWidget(page)

        index = page.report_type_combo.findData(ReportType.PAYROLL_SUMMARY)
        assert index >= 0
        page.report_type_combo.setCurrentIndex(index)

        assert page.year_spin.isEnabled() is True
        assert page.month_combo.isEnabled() is True
        assert page.start_date_edit.isEnabled() is False
        assert page.end_date_edit.isEnabled() is False

    def test_attendance_type_enables_date_range_and_disables_period_picker(
        self, qtbot, company_factory
    ):
        company_id = company_factory()
        page = ReportsPage(company_id=company_id, permission_codes=_full_permissions())
        qtbot.addWidget(page)

        index = page.report_type_combo.findData(ReportType.ATTENDANCE_SUMMARY)
        page.report_type_combo.setCurrentIndex(index)

        assert page.year_spin.isEnabled() is False
        assert page.month_combo.isEnabled() is False
        assert page.start_date_edit.isEnabled() is True
        assert page.end_date_edit.isEnabled() is True


class TestGeneratePayrollReport:
    def test_generate_click_calls_controller_with_year_and_month(
        self, qtbot, monkeypatch, tmp_path, company_factory
    ):
        company_id = company_factory()
        with session_scope() as session:
            EmployeeService(session, company_id=company_id).create_employee(
                employee_number="RP-001", full_name="هدى صالح", salary=Decimal("500000")
            )
        actor_id = _actor_user_id(company_id)

        page = ReportsPage(
            company_id=company_id, current_user_id=actor_id, permission_codes=_full_permissions()
        )
        qtbot.addWidget(page)

        index = page.report_type_combo.findData(ReportType.PAYROLL_DEDUCTIONS)
        page.report_type_combo.setCurrentIndex(index)

        from models.enums import ReportFormat

        page.format_combo.setCurrentIndex(page.format_combo.findData(ReportFormat.CSV))

        today = date.today()
        page.year_spin.setValue(today.year)
        page.month_combo.setCurrentIndex(today.month - 1)

        output_path = tmp_path / "deductions.csv"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(output_path), ""))
        )

        page._on_generate_clicked()

        assert output_path.exists()

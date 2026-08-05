"""Reports screen: build and export attendance reports."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QVBoxLayout,
    QWidget,
)

from controllers.department_controller import DepartmentController
from controllers.employee_controller import EmployeeController
from controllers.report_controller import ReportController
from models.enums import ReportFormat, ReportType
from ui.widgets import (
    Card,
    make_heading_label,
    make_primary_button,
    make_status_label,
    run_blocking_operation,
)

# Only the report types ReportController actually dispatches (BY_DEVICE has
# no build_* method yet, so it is deliberately left out of this picker
# rather than offered as an option that always fails).
_SUPPORTED_REPORT_TYPES = [
    ReportType.ATTENDANCE_SUMMARY,
    ReportType.BY_EMPLOYEE,
    ReportType.BY_DEPARTMENT,
    ReportType.LATE_EMPLOYEES,
    ReportType.OVERTIME,
    ReportType.ABSENCE,
]

_REPORT_TYPES_NEEDING_EMPLOYEE = {ReportType.ATTENDANCE_SUMMARY, ReportType.BY_EMPLOYEE}
_REPORT_TYPES_NEEDING_DEPARTMENT = {
    ReportType.ATTENDANCE_SUMMARY,
    ReportType.BY_EMPLOYEE,
    ReportType.BY_DEPARTMENT,
}

_FORMAT_EXTENSIONS = {
    ReportFormat.EXCEL: "xlsx",
    ReportFormat.PDF: "pdf",
    ReportFormat.CSV: "csv",
}

_FORMAT_FILE_FILTERS = {
    ReportFormat.EXCEL: "Excel Files (*.xlsx)",
    ReportFormat.PDF: "PDF Files (*.pdf)",
    ReportFormat.CSV: "CSV Files (*.csv)",
}


class ReportsPage(QWidget):
    """The report generation/export screen."""

    def __init__(
        self,
        *,
        company_id: int,
        current_user_id: int | None = None,
        permission_codes: frozenset[str] = frozenset(),
        parent: QWidget | None = None,
    ) -> None:
        """Create the reports page.

        Args:
            company_id: The company this screen builds reports for.
            current_user_id: The signed-in user, for audit attribution.
            permission_codes: The signed-in user's granted permission
                codes.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._controller = ReportController(
            company_id=company_id,
            actor_user_id=current_user_id,
            permission_codes=permission_codes,
        )
        self._controller.operation_failed.connect(self._show_error)
        self._controller.report_generated.connect(self._show_success)
        self._employee_controller = EmployeeController(
            company_id=company_id,
            actor_user_id=current_user_id,
            permission_codes=permission_codes,
        )
        self._department_controller = DepartmentController(
            company_id=company_id,
            actor_user_id=current_user_id,
            permission_codes=permission_codes,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addWidget(make_heading_label("التقارير"))

        self._status_label = make_status_label("", "danger")
        self._status_label.hide()
        layout.addWidget(self._status_label)

        card = Card(parent=self)
        form = QFormLayout()
        form.setSpacing(12)

        self.report_type_combo = QComboBox(card)
        for report_type in _SUPPORTED_REPORT_TYPES:
            self.report_type_combo.addItem(report_type.label_ar, userData=report_type)
        self.report_type_combo.currentIndexChanged.connect(self._on_report_type_changed)
        form.addRow("نوع التقرير", self.report_type_combo)

        self.employee_combo = QComboBox(card)
        form.addRow("الموظف", self.employee_combo)

        self.department_combo = QComboBox(card)
        form.addRow("القسم", self.department_combo)

        self.format_combo = QComboBox(card)
        for report_format in ReportFormat:
            self.format_combo.addItem(report_format.label_ar, userData=report_format)
        form.addRow("صيغة الملف", self.format_combo)

        self.start_date_edit = QDateEdit(card)
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate.currentDate().addMonths(-1))
        form.addRow("من تاريخ", self.start_date_edit)

        self.end_date_edit = QDateEdit(card)
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDate(QDate.currentDate())
        form.addRow("إلى تاريخ", self.end_date_edit)

        card.body_layout.addLayout(form)

        generate_button = make_primary_button("توليد التقرير", parent=card)
        generate_button.clicked.connect(self._on_generate_clicked)
        card.body_layout.addWidget(generate_button)

        layout.addWidget(card)
        layout.addStretch(1)

        self._reload_pickers()
        self._on_report_type_changed()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _reload_pickers(self) -> None:
        """Populate the employee/department pickers."""
        self.employee_combo.clear()
        self.employee_combo.addItem("كل الموظفين", userData=None)
        for employee in self._employee_controller.list_employees():
            label = f"{employee['full_name']} ({employee['employee_number']})"
            self.employee_combo.addItem(label, userData=employee["id"])

        self.department_combo.clear()
        self.department_combo.addItem("كل الأقسام", userData=None)
        for department in self._department_controller.list_all():
            self.department_combo.addItem(department["name"], userData=department["id"])

    def _on_report_type_changed(self) -> None:
        """Enable the employee/department pickers only when relevant to the selected type."""
        report_type = ReportType(self.report_type_combo.currentData())
        self.employee_combo.setEnabled(report_type in _REPORT_TYPES_NEEDING_EMPLOYEE)
        self.department_combo.setEnabled(report_type in _REPORT_TYPES_NEEDING_DEPARTMENT)

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def _on_generate_clicked(self) -> None:
        """Prompt for a save location, then generate and export the report."""
        self._hide_status()

        start = self.start_date_edit.date().toPython()
        end = self.end_date_edit.date().toPython()
        if start > end:
            self._show_error("تاريخ البداية يجب أن يسبق تاريخ النهاية.")
            return

        # Qt's QVariant round-trip degrades a BilingualEnum member (a
        # str subclass) back into a plain str, so both must be
        # reconstructed here - report_controller.generate_report() and
        # report_service.export() both dispatch on these with `is`
        # comparisons, which a same-valued plain string would silently
        # fail (falling through to "unsupported type/format").
        report_format = ReportFormat(self.format_combo.currentData())
        output_path = self._prompt_save_path(report_format)
        if output_path is None:
            return

        report_type = ReportType(self.report_type_combo.currentData())
        employee_id = (
            self.employee_combo.currentData()
            if report_type in _REPORT_TYPES_NEEDING_EMPLOYEE
            else None
        )
        department_id = (
            self.department_combo.currentData()
            if report_type in _REPORT_TYPES_NEEDING_DEPARTMENT
            else None
        )

        run_blocking_operation(
            self,
            "جارٍ توليد التقرير...",
            lambda: self._controller.generate_report(
                report_type=report_type,
                output_format=report_format,
                output_path=output_path,
                start_date=start,
                end_date=end,
                employee_id=employee_id,
                department_id=department_id,
            ),
        )

    def _prompt_save_path(self, report_format: ReportFormat) -> Path | None:
        """Ask the user where to save the report, defaulting to the right extension.

        Args:
            report_format: The selected export format, used to pick the
                default extension and file-dialog filter.

        Returns:
            The chosen path (extension enforced), or ``None`` if the
            user cancelled.
        """
        extension = _FORMAT_EXTENSIONS[report_format]
        default_name = f"report.{extension}"
        file_filter = _FORMAT_FILE_FILTERS[report_format]
        chosen, _selected_filter = QFileDialog.getSaveFileName(
            self, "حفظ التقرير", default_name, file_filter
        )
        if not chosen:
            return None
        path = Path(chosen)
        if path.suffix.lower() != f".{extension}":
            path = path.with_suffix(f".{extension}")
        return path

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _show_success(self, output_path: str) -> None:
        """Show a success banner with the exported file's path."""
        self._set_status_variant("success")
        self._status_label.setText(f"تم إنشاء التقرير بنجاح: {output_path}")
        self._status_label.show()

    def _show_error(self, message: str) -> None:
        """Show an error banner."""
        self._set_status_variant("danger")
        self._status_label.setText(message)
        self._status_label.show()

    def _set_status_variant(self, status: str) -> None:
        """Re-apply the status label's dynamic ``status`` QSS property.

        Qt does not automatically re-evaluate stylesheet selectors that
        key off a dynamic property after it changes on an already-shown
        widget; unpolish/polish forces the style engine to re-match it.
        """
        self._status_label.setProperty("status", status)
        style = self._status_label.style()
        style.unpolish(self._status_label)
        style.polish(self._status_label)

    def _hide_status(self) -> None:
        """Hide the status banner."""
        self._status_label.clear()
        self._status_label.hide()

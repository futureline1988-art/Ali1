"""Employees screen: list, search, and manage employee records."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QPlainTextEdit,
    QWidget,
)

from controllers.department_controller import DepartmentController
from controllers.employee_controller import EmployeeController
from ui.table_page import TablePage
from ui.widgets import ConfirmDialog, make_danger_button


class EmployeeFormDialog(QDialog):
    """Add/edit form for a single employee.

    Construct with ``existing=None`` for "add" mode, or an employee
    dict (as returned by
    :class:`~controllers.employee_controller.EmployeeController`) for
    "edit" mode, which pre-fills every field.
    """

    def __init__(
        self,
        *,
        departments: list[dict[str, Any]],
        existing: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Build the form dialog.

        Args:
            departments: Available departments (``{"id", "name", ...}``
                dicts) to populate the department picker.
            existing: The employee being edited, or ``None`` to create
                a new one.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._existing = existing
        self.setWindowTitle("تعديل بيانات موظف" if existing else "إضافة موظف جديد")
        self.setMinimumWidth(420)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.employee_number_edit = QLineEdit(self)
        form.addRow("الرقم الوظيفي *", self.employee_number_edit)

        self.full_name_edit = QLineEdit(self)
        form.addRow("الاسم الكامل *", self.full_name_edit)

        self.department_combo = QComboBox(self)
        self.department_combo.addItem("بدون قسم", userData=None)
        for department in departments:
            self.department_combo.addItem(department["name"], userData=department["id"])
        form.addRow("القسم", self.department_combo)

        self.national_id_edit = QLineEdit(self)
        form.addRow("الرقم الوطني", self.national_id_edit)

        self.email_edit = QLineEdit(self)
        form.addRow("البريد الإلكتروني", self.email_edit)

        self.phone_edit = QLineEdit(self)
        form.addRow("الهاتف", self.phone_edit)

        self.position_edit = QLineEdit(self)
        form.addRow("المسمى الوظيفي", self.position_edit)

        self.salary_spin = QDoubleSpinBox(self)
        self.salary_spin.setRange(0, 999_999_999)
        self.salary_spin.setDecimals(2)
        self.salary_spin.setSuffix(" د.ع")
        form.addRow("الراتب", self.salary_spin)

        self.hire_date_edit = QDateEdit(self)
        self.hire_date_edit.setCalendarPopup(True)
        self.hire_date_edit.setDate(QDate.currentDate())
        form.addRow("تاريخ التعيين", self.hire_date_edit)

        self.notes_edit = QPlainTextEdit(self)
        self.notes_edit.setFixedHeight(70)
        form.addRow("ملاحظات", self.notes_edit)

        if existing is not None:
            self._apply_existing(existing)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.Save).setText("حفظ")
        buttons.button(QDialogButtonBox.Cancel).setText("إلغاء")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _apply_existing(self, existing: dict[str, Any]) -> None:
        """Pre-fill every field from an existing employee's data."""
        self.employee_number_edit.setText(existing.get("employee_number") or "")
        self.full_name_edit.setText(existing.get("full_name") or "")

        department_id = existing.get("department_id")
        index = self.department_combo.findData(department_id)
        self.department_combo.setCurrentIndex(index if index >= 0 else 0)

        self.national_id_edit.setText(existing.get("national_id") or "")
        self.email_edit.setText(existing.get("email") or "")
        self.phone_edit.setText(existing.get("phone") or "")
        self.position_edit.setText(existing.get("position") or "")

        salary = existing.get("salary")
        if salary is not None:
            self.salary_spin.setValue(float(salary))

        hire_date_value = existing.get("hire_date")
        if hire_date_value:
            parsed = date.fromisoformat(hire_date_value)
            self.hire_date_edit.setDate(QDate(parsed.year, parsed.month, parsed.day))

        self.notes_edit.setPlainText(existing.get("notes") or "")

    def values(self) -> dict[str, Any]:
        """Read the form's current state as keyword arguments for the controller.

        Returns:
            A dict compatible with
            :meth:`~controllers.employee_controller.EmployeeController.create_employee`
            / ``update_employee``.
        """
        salary_value = self.salary_spin.value()
        try:
            salary = Decimal(str(salary_value)) if salary_value > 0 else None
        except InvalidOperation:
            salary = None

        qdate = self.hire_date_edit.date()
        hire_date = date(qdate.year(), qdate.month(), qdate.day())

        return {
            "employee_number": self.employee_number_edit.text().strip(),
            "full_name": self.full_name_edit.text().strip(),
            "department_id": self.department_combo.currentData(),
            "national_id": self.national_id_edit.text().strip() or None,
            "email": self.email_edit.text().strip() or None,
            "phone": self.phone_edit.text().strip() or None,
            "position": self.position_edit.text().strip() or None,
            "salary": salary,
            "hire_date": hire_date,
            "notes": self.notes_edit.toPlainText().strip() or None,
        }


class EmployeesPage(TablePage):
    """The employees management screen."""

    def __init__(self, *, company_id: int, parent: QWidget | None = None) -> None:
        """Create the employees page.

        Args:
            company_id: The company this screen manages employees for.
            parent: Optional parent widget.
        """
        super().__init__(
            title="الموظفون",
            add_button_text="+ إضافة موظف",
            search_placeholder="بحث بالاسم...",
            parent=parent,
        )
        self._company_id = company_id
        self._controller = EmployeeController(company_id=company_id)
        self._controller.operation_failed.connect(self.show_error)
        self._department_controller = DepartmentController(company_id=company_id)

        self.delete_button = make_danger_button("حذف", parent=self)
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self._on_delete_clicked)
        self.toolbar_layout.addWidget(self.delete_button)

        self.set_columns(
            [
                ("employee_number", "الرقم الوظيفي", lambda row: row.get("employee_number") or ""),
                ("full_name", "الاسم الكامل", lambda row: row.get("full_name") or ""),
                ("department_name", "القسم", lambda row: row.get("department_name") or "—"),
                ("position", "المسمى الوظيفي", lambda row: row.get("position") or "—"),
                ("phone", "الهاتف", lambda row: row.get("phone") or "—"),
                (
                    "employment_status_label_ar",
                    "الحالة",
                    lambda row: row.get("employment_status_label_ar") or "",
                ),
            ]
        )

        self.add_requested.connect(self._on_add_clicked)
        self.row_activated.connect(self._on_edit_row)
        self.search_changed.connect(self._on_search_changed)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        self.refresh()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Reload the employee list from the database."""
        self.clear_error()
        rows = self._controller.list_employees()
        self.populate(rows)
        self._on_selection_changed()

    def _on_search_changed(self, query: str) -> None:
        """Reload the table filtered by ``query`` (or all employees if empty)."""
        self.clear_error()
        rows = (
            self._controller.search_employees(query)
            if query.strip()
            else self._controller.list_employees()
        )
        self.populate(rows)
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        """Enable/disable the delete button based on the current selection."""
        self.delete_button.setEnabled(self.selected_row() is not None)

    # ------------------------------------------------------------------
    # Add / edit / delete
    # ------------------------------------------------------------------

    def _load_department_choices(self) -> list[dict[str, Any]]:
        """Fetch every department for the form's department picker."""
        return self._department_controller.list_all()

    def _on_add_clicked(self) -> None:
        """Open the "add employee" dialog and persist the result if accepted."""
        dialog = EmployeeFormDialog(departments=self._load_department_choices(), parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["employee_number"] or not values["full_name"]:
            self.show_error("الرقم الوظيفي والاسم الكامل حقلان إلزاميان.")
            return
        result = self._controller.create_employee(**values)
        if result is not None:
            self.refresh()

    def _on_edit_row(self, row: dict[str, Any]) -> None:
        """Open the "edit employee" dialog for ``row`` and persist changes."""
        dialog = EmployeeFormDialog(
            departments=self._load_department_choices(), existing=row, parent=self
        )
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["employee_number"] or not values["full_name"]:
            self.show_error("الرقم الوظيفي والاسم الكامل حقلان إلزاميان.")
            return
        result = self._controller.update_employee(row["id"], **values)
        if result is not None:
            self.refresh()

    def _on_delete_clicked(self) -> None:
        """Confirm and delete the currently selected employee."""
        row = self.selected_row()
        if row is None:
            return
        confirmed = ConfirmDialog.confirm(
            self,
            "تأكيد حذف الموظف",
            f"هل أنت متأكد من حذف الموظف \"{row['full_name']}\"؟",
            danger=True,
        )
        if not confirmed:
            return
        if self._controller.delete_employee(row["id"]):
            self.refresh()

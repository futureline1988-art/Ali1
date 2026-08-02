"""Shifts screen: manage shift definitions and employee shift assignments."""

from __future__ import annotations

from datetime import date as date_cls
from datetime import time as time_cls
from typing import Any

from PySide6.QtCore import QDate, QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTimeEdit,
    QWidget,
)

from controllers.shift_controller import ShiftController
from models.enums import Weekday
from ui.table_page import TablePage
from ui.widgets import ConfirmDialog, make_danger_button

_WEEKDAY_ORDER = list(Weekday)


def _time_to_qtime(value: time_cls | str) -> QTime:
    """Convert a ``datetime.time`` or ``"HH:MM"`` string into a :class:`QTime`."""
    if isinstance(value, str):
        hour, minute = (int(part) for part in value.split(":")[:2])
        return QTime(hour, minute)
    return QTime(value.hour, value.minute)


class ShiftFormDialog(QDialog):
    """Add/edit form for a single shift."""

    def __init__(
        self,
        *,
        existing: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Build the form dialog.

        Args:
            existing: The shift being edited, or ``None`` to create a
                new one.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("تعديل وردية" if existing else "إضافة وردية جديدة")
        self.setMinimumWidth(440)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.name_edit = QLineEdit(self)
        form.addRow("اسم الوردية *", self.name_edit)

        self.start_time_edit = QTimeEdit(self)
        self.start_time_edit.setDisplayFormat("HH:mm")
        self.start_time_edit.setTime(QTime(8, 0))
        form.addRow("وقت البدء *", self.start_time_edit)

        self.end_time_edit = QTimeEdit(self)
        self.end_time_edit.setDisplayFormat("HH:mm")
        self.end_time_edit.setTime(QTime(16, 0))
        form.addRow("وقت الانتهاء *", self.end_time_edit)

        self.grace_spin = QSpinBox(self)
        self.grace_spin.setRange(0, 240)
        self.grace_spin.setSuffix(" دقيقة")
        form.addRow("سماحية التأخير", self.grace_spin)

        self.early_leave_spin = QSpinBox(self)
        self.early_leave_spin.setRange(0, 240)
        self.early_leave_spin.setSuffix(" دقيقة")
        form.addRow("سماحية الانصراف المبكر", self.early_leave_spin)

        self.overtime_spin = QSpinBox(self)
        self.overtime_spin.setRange(0, 480)
        self.overtime_spin.setSuffix(" دقيقة")
        form.addRow("حد احتساب الوقت الإضافي", self.overtime_spin)

        days_group = QGroupBox("أيام العمل", self)
        days_grid = QGridLayout(days_group)
        self.day_checkboxes: dict[Weekday, QCheckBox] = {}
        for index, weekday in enumerate(_WEEKDAY_ORDER):
            checkbox = QCheckBox(weekday.label_ar, days_group)
            checkbox.setChecked(True)
            self.day_checkboxes[weekday] = checkbox
            days_grid.addWidget(checkbox, index // 4, index % 4)
        form.addRow(days_group)

        self.active_checkbox = QCheckBox("وردية نشطة (قابلة للتعيين)", self)
        self.active_checkbox.setChecked(True)
        form.addRow(self.active_checkbox)

        self.description_edit = QPlainTextEdit(self)
        self.description_edit.setFixedHeight(70)
        form.addRow("الوصف", self.description_edit)

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
        """Pre-fill every field from an existing shift's data."""
        self.name_edit.setText(existing.get("name") or "")
        if existing.get("start_time"):
            self.start_time_edit.setTime(_time_to_qtime(existing["start_time"]))
        if existing.get("end_time"):
            self.end_time_edit.setTime(_time_to_qtime(existing["end_time"]))
        self.grace_spin.setValue(int(existing.get("grace_period_minutes") or 0))
        self.early_leave_spin.setValue(int(existing.get("early_leave_grace_minutes") or 0))
        self.overtime_spin.setValue(int(existing.get("overtime_threshold_minutes") or 0))
        active_days = set(existing.get("working_days") or [])
        for weekday, checkbox in self.day_checkboxes.items():
            checkbox.setChecked(weekday.value in active_days)
        self.active_checkbox.setChecked(bool(existing.get("is_active", True)))
        self.description_edit.setPlainText(existing.get("description") or "")

    def values(self) -> dict[str, Any]:
        """Read the form's current state as keyword arguments for the controller.

        Returns:
            A dict compatible with
            :meth:`~controllers.shift_controller.ShiftController.create_shift`
            / ``update_shift``.
        """
        start = self.start_time_edit.time()
        end = self.end_time_edit.time()
        return {
            "name": self.name_edit.text().strip(),
            "start_time": time_cls(start.hour(), start.minute()),
            "end_time": time_cls(end.hour(), end.minute()),
            "grace_period_minutes": self.grace_spin.value(),
            "early_leave_grace_minutes": self.early_leave_spin.value(),
            "overtime_threshold_minutes": self.overtime_spin.value(),
            "working_days": [
                weekday.value
                for weekday, checkbox in self.day_checkboxes.items()
                if checkbox.isChecked()
            ],
            "is_active": self.active_checkbox.isChecked(),
            "description": self.description_edit.toPlainText().strip() or None,
        }


class AssignEmployeeDialog(QDialog):
    """A focused dialog for assigning an employee to a shift."""

    def __init__(
        self,
        *,
        shift: dict[str, Any],
        employees: list[dict[str, Any]],
        parent: QWidget | None = None,
    ) -> None:
        """Build the dialog.

        Args:
            shift: The shift employees will be assigned to.
            employees: Active employees available to assign.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle(f"تعيين موظف إلى: {shift['name']}")
        self.setMinimumWidth(380)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.employee_combo = QComboBox(self)
        for employee in employees:
            label = f"{employee['full_name']} ({employee['employee_number']})"
            self.employee_combo.addItem(label, userData=employee["id"])
        form.addRow("الموظف *", self.employee_combo)

        self.effective_from_edit = QDateEdit(self)
        self.effective_from_edit.setCalendarPopup(True)
        self.effective_from_edit.setDate(QDate.currentDate())
        form.addRow("تاريخ السريان *", self.effective_from_edit)

        self.notes_edit = QPlainTextEdit(self)
        self.notes_edit.setFixedHeight(60)
        form.addRow("ملاحظات", self.notes_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.Ok).setText("تعيين")
        buttons.button(QDialogButtonBox.Cancel).setText("إلغاء")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict[str, Any]:
        """Read the form's current state for
        :meth:`~controllers.shift_controller.ShiftController.assign_employee`.
        """
        qdate = self.effective_from_edit.date()
        return {
            "employee_id": self.employee_combo.currentData(),
            "effective_from": date_cls(qdate.year(), qdate.month(), qdate.day()),
            "notes": self.notes_edit.toPlainText().strip() or None,
        }


def _format_working_days(row: dict[str, Any]) -> str:
    """Render a shift's ``working_days`` as a compact Arabic label list."""
    active = set(row.get("working_days") or [])
    if len(active) == 7:
        return "كل الأيام"
    labels = [weekday.label_ar for weekday in _WEEKDAY_ORDER if weekday.value in active]
    return "، ".join(labels) if labels else "—"


class ShiftsPage(TablePage):
    """The shift definitions and employee-assignment management screen."""

    def __init__(
        self,
        *,
        company_id: int,
        current_user_id: int | None = None,
        permission_codes: frozenset[str] = frozenset(),
        parent: QWidget | None = None,
    ) -> None:
        """Create the shifts page.

        Args:
            company_id: The company this screen manages shifts for.
            current_user_id: The signed-in user, for audit attribution.
            permission_codes: The signed-in user's granted permission
                codes.
            parent: Optional parent widget.
        """
        super().__init__(
            title="الورديات",
            add_button_text="+ إضافة وردية",
            search_placeholder="بحث بالاسم...",
            parent=parent,
        )
        self._controller = ShiftController(
            company_id=company_id,
            actor_user_id=current_user_id,
            permission_codes=permission_codes,
        )
        self._controller.operation_failed.connect(self.show_error)

        self.assign_button = _secondary_button("تعيين موظف")
        self.assign_button.clicked.connect(self._on_assign_clicked)
        self.toolbar_layout.addWidget(self.assign_button)

        self.edit_button = _secondary_button("تعديل")
        self.edit_button.clicked.connect(self._on_edit_clicked)
        self.toolbar_layout.addWidget(self.edit_button)

        self.delete_button = make_danger_button("حذف", parent=self)
        self.delete_button.clicked.connect(self._on_delete_clicked)
        self.toolbar_layout.addWidget(self.delete_button)

        self.set_columns(
            [
                ("name", "اسم الوردية", lambda row: row.get("name") or ""),
                (
                    "time_range",
                    "الوقت",
                    lambda row: f"{row.get('start_time', '')} - {row.get('end_time', '')}",
                ),
                (
                    "duration",
                    "المدة",
                    lambda row: f"{(row.get('duration_minutes') or 0) // 60} ساعة",
                ),
                ("working_days", "أيام العمل", _format_working_days),
                (
                    "is_active",
                    "نشطة",
                    lambda row: "نعم" if row.get("is_active") else "لا",
                ),
            ]
        )

        self.add_requested.connect(self._on_add_clicked)
        self.row_activated.connect(lambda _row: self._on_edit_clicked())
        self.search_changed.connect(self._on_search_changed)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        self._all_rows: list[dict[str, Any]] = []
        self.refresh()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Reload the shift list from the database."""
        self.clear_error()
        self._all_rows = self._controller.list_shifts()
        self.populate(self._all_rows)
        self._on_selection_changed()

    def _on_search_changed(self, query: str) -> None:
        """Filter the currently loaded shifts by name (client-side)."""
        self.clear_error()
        rows = self._all_rows
        if query.strip():
            needle = query.strip().lower()
            rows = [row for row in rows if needle in (row.get("name") or "").lower()]
        self.populate(rows)
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        """Enable/disable selection-dependent buttons based on the current selection."""
        has_selection = self.selected_row() is not None
        self.assign_button.setEnabled(has_selection)
        self.edit_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)

    # ------------------------------------------------------------------
    # Add / edit / delete
    # ------------------------------------------------------------------

    def _on_add_clicked(self) -> None:
        """Open the "add shift" dialog and persist the result if accepted."""
        dialog = ShiftFormDialog(parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["name"]:
            self.show_error("اسم الوردية حقل إلزامي.")
            return
        result = self._controller.create_shift(**values)
        if result is not None:
            self.refresh()

    def _on_edit_clicked(self) -> None:
        """Open the "edit shift" dialog for the selected row and persist changes."""
        row = self.selected_row()
        if row is None:
            return
        dialog = ShiftFormDialog(existing=row, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["name"]:
            self.show_error("اسم الوردية حقل إلزامي.")
            return
        result = self._controller.update_shift(row["id"], **values)
        if result is not None:
            self.refresh()

    def _on_delete_clicked(self) -> None:
        """Confirm and delete the currently selected shift."""
        row = self.selected_row()
        if row is None:
            return
        confirmed = ConfirmDialog.confirm(
            self,
            "تأكيد حذف الوردية",
            f"هل أنت متأكد من حذف الوردية \"{row['name']}\"؟",
            danger=True,
        )
        if not confirmed:
            return
        if self._controller.delete_shift(row["id"]):
            self.refresh()

    # ------------------------------------------------------------------
    # Assignment
    # ------------------------------------------------------------------

    def _on_assign_clicked(self) -> None:
        """Open the employee picker and assign the chosen employee to the selected shift."""
        row = self.selected_row()
        if row is None:
            return
        employees = self._controller.list_employees_for_assignment()
        if not employees:
            self.show_error("لا يوجد موظفون نشطون لتعيينهم.")
            return
        dialog = AssignEmployeeDialog(shift=row, employees=employees, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if values["employee_id"] is None:
            return
        result = self._controller.assign_employee(shift_id=row["id"], **values)
        if result is not None:
            self.clear_error()


def _secondary_button(text: str) -> QPushButton:
    """Create a disabled-by-default secondary toolbar button.

    Args:
        text: The button's label.

    Returns:
        A plain :class:`~PySide6.QtWidgets.QPushButton`, initially
        disabled (enabled once a table row is selected).
    """
    button = QPushButton(text)
    button.setEnabled(False)
    return button

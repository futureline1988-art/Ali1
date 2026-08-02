"""Holidays screen: manage the company's official holiday calendar."""

from __future__ import annotations

from datetime import date as date_cls
from typing import Any

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QPlainTextEdit,
    QWidget,
)

from controllers.holiday_controller import HolidayController
from ui.table_page import TablePage
from ui.widgets import ConfirmDialog, make_danger_button


class HolidayFormDialog(QDialog):
    """Add/edit form for a single holiday."""

    def __init__(
        self,
        *,
        existing: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Build the form dialog.

        Args:
            existing: The holiday being edited, or ``None`` to create a
                new one.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("تعديل عطلة" if existing else "إضافة عطلة جديدة")
        self.setMinimumWidth(400)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.name_edit = QLineEdit(self)
        form.addRow("اسم العطلة *", self.name_edit)

        self.date_edit = QDateEdit(self)
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(QDate.currentDate())
        form.addRow("التاريخ *", self.date_edit)

        self.recurring_checkbox = QCheckBox("تتكرر سنويًا (نفس الشهر واليوم كل سنة)", self)
        form.addRow(self.recurring_checkbox)

        self.active_checkbox = QCheckBox("عطلة نشطة (تُحتسب في الحضور)", self)
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
        """Pre-fill every field from an existing holiday's data."""
        self.name_edit.setText(existing.get("name") or "")
        holiday_date = existing.get("holiday_date")
        if holiday_date:
            year, month, day = (int(part) for part in str(holiday_date).split("-")[:3])
            self.date_edit.setDate(QDate(year, month, day))
        self.recurring_checkbox.setChecked(bool(existing.get("is_recurring_annually")))
        self.active_checkbox.setChecked(bool(existing.get("is_active", True)))
        self.description_edit.setPlainText(existing.get("description") or "")

    def values(self) -> dict[str, Any]:
        """Read the form's current state as keyword arguments for the controller.

        Returns:
            A dict compatible with
            :meth:`~controllers.holiday_controller.HolidayController.create_holiday`
            / ``update_holiday``.
        """
        qdate = self.date_edit.date()
        return {
            "name": self.name_edit.text().strip(),
            "holiday_date": date_cls(qdate.year(), qdate.month(), qdate.day()),
            "is_recurring_annually": self.recurring_checkbox.isChecked(),
            "is_active": self.active_checkbox.isChecked(),
            "description": self.description_edit.toPlainText().strip() or None,
        }


def _format_recurrence(row: dict[str, Any]) -> str:
    """Render a holiday's recurrence as a short Arabic label."""
    return "سنوية" if row.get("is_recurring_annually") else "مرة واحدة"


class HolidaysPage(TablePage):
    """The holiday calendar management screen."""

    def __init__(
        self,
        *,
        company_id: int,
        current_user_id: int | None = None,
        permission_codes: frozenset[str] = frozenset(),
        parent: QWidget | None = None,
    ) -> None:
        """Create the holidays page.

        Args:
            company_id: The company this screen manages the holiday
                calendar for.
            current_user_id: The signed-in user, for audit attribution.
            permission_codes: The signed-in user's granted permission
                codes.
            parent: Optional parent widget.
        """
        super().__init__(
            title="العطلات الرسمية",
            add_button_text="+ إضافة عطلة",
            search_placeholder="بحث بالاسم...",
            parent=parent,
        )
        self._controller = HolidayController(
            company_id=company_id,
            actor_user_id=current_user_id,
            permission_codes=permission_codes,
        )
        self._controller.operation_failed.connect(self.show_error)

        self.delete_button = make_danger_button("حذف", parent=self)
        self.delete_button.clicked.connect(self._on_delete_clicked)
        self.toolbar_layout.addWidget(self.delete_button)

        self.set_columns(
            [
                ("name", "اسم العطلة", lambda row: row.get("name") or ""),
                ("holiday_date", "التاريخ", lambda row: row.get("holiday_date") or ""),
                ("recurrence", "التكرار", _format_recurrence),
                (
                    "is_active",
                    "نشطة",
                    lambda row: "نعم" if row.get("is_active") else "لا",
                ),
            ]
        )

        self.add_requested.connect(self._on_add_clicked)
        self.row_activated.connect(self._on_edit_row)
        self.search_changed.connect(self._on_search_changed)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        self._all_rows: list[dict[str, Any]] = []
        self.refresh()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Reload the holiday list from the database."""
        self.clear_error()
        self._all_rows = sorted(
            self._controller.list_holidays(), key=lambda row: row.get("holiday_date") or ""
        )
        self.populate(self._all_rows)
        self._on_selection_changed()

    def _on_search_changed(self, query: str) -> None:
        """Filter the currently loaded holidays by name (client-side)."""
        self.clear_error()
        rows = self._all_rows
        if query.strip():
            needle = query.strip().lower()
            rows = [row for row in rows if needle in (row.get("name") or "").lower()]
        self.populate(rows)
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        """Enable/disable the delete button based on the current selection."""
        self.delete_button.setEnabled(self.selected_row() is not None)

    # ------------------------------------------------------------------
    # Add / edit / delete
    # ------------------------------------------------------------------

    def _on_add_clicked(self) -> None:
        """Open the "add holiday" dialog and persist the result if accepted."""
        dialog = HolidayFormDialog(parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["name"]:
            self.show_error("اسم العطلة حقل إلزامي.")
            return
        result = self._controller.create_holiday(**values)
        if result is not None:
            self.refresh()

    def _on_edit_row(self, row: dict[str, Any]) -> None:
        """Open the "edit holiday" dialog for ``row`` and persist changes."""
        dialog = HolidayFormDialog(existing=row, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["name"]:
            self.show_error("اسم العطلة حقل إلزامي.")
            return
        result = self._controller.update_holiday(row["id"], **values)
        if result is not None:
            self.refresh()

    def _on_delete_clicked(self) -> None:
        """Confirm and delete the currently selected holiday."""
        row = self.selected_row()
        if row is None:
            return
        confirmed = ConfirmDialog.confirm(
            self,
            "تأكيد حذف العطلة",
            f"هل أنت متأكد من حذف عطلة \"{row['name']}\"؟",
            danger=True,
        )
        if not confirmed:
            return
        if self._controller.delete_holiday(row["id"]):
            self.refresh()

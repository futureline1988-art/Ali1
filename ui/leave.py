"""Leave screen: manage leave policies and the leave-request workflow."""

from __future__ import annotations

from datetime import date as date_cls
from typing import Any

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QWidget,
)

from controllers.employee_controller import EmployeeController
from controllers.leave_controller import LeaveController
from models.enums import LeaveType
from ui.table_page import TablePage
from ui.widgets import ConfirmDialog, make_danger_button

_LEAVE_TYPE_ORDER = list(LeaveType)


# ----------------------------------------------------------------------
# Policies tab
# ----------------------------------------------------------------------


class LeavePolicyFormDialog(QDialog):
    """Add/edit form for a single leave policy."""

    def __init__(
        self,
        *,
        existing: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Build the form dialog.

        Args:
            existing: The policy being edited, or ``None`` to create a
                new one.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("تعديل سياسة إجازة" if existing else "إضافة سياسة إجازة جديدة")
        self.setMinimumWidth(400)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.leave_type_combo = QComboBox(self)
        for leave_type in _LEAVE_TYPE_ORDER:
            self.leave_type_combo.addItem(leave_type.label_ar, userData=leave_type)
        self.leave_type_combo.setEnabled(existing is None)
        form.addRow("نوع الإجازة *", self.leave_type_combo)

        self.name_edit = QLineEdit(self)
        form.addRow("اسم السياسة *", self.name_edit)

        self.entitlement_spin = QSpinBox(self)
        self.entitlement_spin.setRange(0, 365)
        self.entitlement_spin.setSpecialValueText("غير محدد")
        form.addRow("الرصيد السنوي (أيام)", self.entitlement_spin)

        self.paid_checkbox = QCheckBox("إجازة مدفوعة الأجر", self)
        self.paid_checkbox.setChecked(True)
        form.addRow(self.paid_checkbox)

        self.requires_approval_checkbox = QCheckBox("تتطلب موافقة", self)
        self.requires_approval_checkbox.setChecked(True)
        form.addRow(self.requires_approval_checkbox)

        self.active_checkbox = QCheckBox("سياسة نشطة (متاحة للطلب)", self)
        self.active_checkbox.setChecked(True)
        form.addRow(self.active_checkbox)

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
        """Pre-fill every field from an existing policy's data."""
        leave_type_value = existing.get("leave_type")
        if leave_type_value is not None:
            index = self.leave_type_combo.findData(LeaveType(leave_type_value))
            if index >= 0:
                self.leave_type_combo.setCurrentIndex(index)
        self.name_edit.setText(existing.get("name") or "")
        entitlement = existing.get("annual_entitlement_days")
        self.entitlement_spin.setValue(int(entitlement) if entitlement is not None else 0)
        self.paid_checkbox.setChecked(bool(existing.get("is_paid", True)))
        self.requires_approval_checkbox.setChecked(bool(existing.get("requires_approval", True)))
        self.active_checkbox.setChecked(bool(existing.get("is_active", True)))

    def values(self) -> dict[str, Any]:
        """Read the form's current state as keyword arguments for the controller.

        Returns:
            A dict compatible with
            :meth:`~controllers.leave_controller.LeaveController.create_policy`
            / ``update_policy``.
        """
        entitlement = self.entitlement_spin.value()
        return {
            # Qt's QVariant round-trip degrades a BilingualEnum member (a
            # str subclass) back into a plain str, so it must be
            # reconstructed into a real LeaveType here rather than
            # trusting currentData()'s type - see DeviceFormDialog.values()
            # in ui/devices.py for the same pitfall.
            "leave_type": LeaveType(self.leave_type_combo.currentData()),
            "name": self.name_edit.text().strip(),
            "annual_entitlement_days": entitlement if entitlement > 0 else None,
            "is_paid": self.paid_checkbox.isChecked(),
            "requires_approval": self.requires_approval_checkbox.isChecked(),
            "is_active": self.active_checkbox.isChecked(),
        }


class LeavePoliciesTab(TablePage):
    """The leave-policy configuration list, nested inside :class:`LeavePage`."""

    def __init__(
        self,
        *,
        company_id: int,
        current_user_id: int | None = None,
        permission_codes: frozenset[str] = frozenset(),
        parent: QWidget | None = None,
    ) -> None:
        """Create the policies tab.

        Args:
            company_id: The company this tab manages leave policies for.
            current_user_id: The signed-in user, for audit attribution.
            permission_codes: The signed-in user's granted permission
                codes.
            parent: Optional parent widget.
        """
        super().__init__(title="سياسات الإجازات", add_button_text="+ إضافة سياسة", parent=parent)
        self._controller = LeaveController(
            company_id=company_id,
            actor_user_id=current_user_id,
            permission_codes=permission_codes,
        )
        self._controller.operation_failed.connect(self.show_error)

        self.edit_button = _secondary_button("تعديل")
        self.edit_button.clicked.connect(self._on_edit_clicked)
        self.toolbar_layout.addWidget(self.edit_button)

        self.delete_button = make_danger_button("حذف", parent=self)
        self.delete_button.clicked.connect(self._on_delete_clicked)
        self.toolbar_layout.addWidget(self.delete_button)

        self.set_columns(
            [
                ("name", "اسم السياسة", lambda row: row.get("name") or ""),
                (
                    "leave_type",
                    "النوع",
                    lambda row: row.get("leave_type_label_ar") or "",
                ),
                (
                    "annual_entitlement_days",
                    "الرصيد السنوي",
                    lambda row: str(row.get("annual_entitlement_days") or "غير محدد"),
                ),
                (
                    "is_paid",
                    "مدفوعة",
                    lambda row: "نعم" if row.get("is_paid") else "لا",
                ),
                (
                    "requires_approval",
                    "تتطلب موافقة",
                    lambda row: "نعم" if row.get("requires_approval") else "لا",
                ),
                (
                    "is_active",
                    "نشطة",
                    lambda row: "نعم" if row.get("is_active") else "لا",
                ),
            ]
        )

        self.add_requested.connect(self._on_add_clicked)
        self.row_activated.connect(self._on_edit_row)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        self.refresh()

    def refresh(self) -> None:
        """Reload the policy list from the database."""
        self.clear_error()
        self.populate(self._controller.list_policies())
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        """Enable/disable selection-dependent buttons based on the current selection."""
        has_selection = self.selected_row() is not None
        self.edit_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)

    def _on_add_clicked(self) -> None:
        """Open the "add policy" dialog and persist the result if accepted."""
        dialog = LeavePolicyFormDialog(parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["name"]:
            self.show_error("اسم السياسة حقل إلزامي.")
            return
        if self._controller.create_policy(**values) is not None:
            self.refresh()

    def _on_edit_clicked(self) -> None:
        """Open the "edit policy" dialog for the selected row."""
        row = self.selected_row()
        if row is not None:
            self._on_edit_row(row)

    def _on_edit_row(self, row: dict[str, Any]) -> None:
        """Open the "edit policy" dialog for ``row`` and persist changes."""
        dialog = LeavePolicyFormDialog(existing=row, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["name"]:
            self.show_error("اسم السياسة حقل إلزامي.")
            return
        values.pop("leave_type", None)
        if self._controller.update_policy(row["id"], **values) is not None:
            self.refresh()

    def _on_delete_clicked(self) -> None:
        """Confirm and delete the currently selected policy."""
        row = self.selected_row()
        if row is None:
            return
        confirmed = ConfirmDialog.confirm(
            self,
            "تأكيد حذف السياسة",
            f"هل أنت متأكد من حذف سياسة \"{row['name']}\"؟",
            danger=True,
        )
        if not confirmed:
            return
        if self._controller.delete_policy(row["id"]):
            self.refresh()

    def list_active_policies(self) -> list[dict[str, Any]]:
        """List active policies, for the request-submission form."""
        return self._controller.list_active_policies()


# ----------------------------------------------------------------------
# Requests tab
# ----------------------------------------------------------------------


class LeaveRequestFormDialog(QDialog):
    """Submission form for a new leave request."""

    def __init__(
        self,
        *,
        employees: list[dict[str, Any]],
        policies: list[dict[str, Any]],
        parent: QWidget | None = None,
    ) -> None:
        """Build the dialog.

        Args:
            employees: Active employees available to submit for.
            policies: Active leave policies available to request under.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("تقديم طلب إجازة")
        self.setMinimumWidth(420)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.employee_combo = QComboBox(self)
        for employee in employees:
            label = f"{employee['full_name']} ({employee['employee_number']})"
            self.employee_combo.addItem(label, userData=employee["id"])
        form.addRow("الموظف *", self.employee_combo)

        self.policy_combo = QComboBox(self)
        for policy in policies:
            self.policy_combo.addItem(policy["name"], userData=policy["id"])
        form.addRow("نوع الإجازة *", self.policy_combo)

        self.start_date_edit = QDateEdit(self)
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate.currentDate())
        form.addRow("من تاريخ *", self.start_date_edit)

        self.end_date_edit = QDateEdit(self)
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDate(QDate.currentDate())
        form.addRow("إلى تاريخ *", self.end_date_edit)

        self.reason_edit = QPlainTextEdit(self)
        self.reason_edit.setFixedHeight(60)
        form.addRow("السبب", self.reason_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.Ok).setText("تقديم")
        buttons.button(QDialogButtonBox.Cancel).setText("إلغاء")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict[str, Any]:
        """Read the form's current state for
        :meth:`~controllers.leave_controller.LeaveController.submit_request`.
        """
        start = self.start_date_edit.date()
        end = self.end_date_edit.date()
        return {
            "employee_id": self.employee_combo.currentData(),
            "leave_policy_id": self.policy_combo.currentData(),
            "start_date": date_cls(start.year(), start.month(), start.day()),
            "end_date": date_cls(end.year(), end.month(), end.day()),
            "reason": self.reason_edit.toPlainText().strip() or None,
        }


class ReviewNotesDialog(QDialog):
    """A small free-text prompt for approve/reject notes."""

    def __init__(self, *, title: str, parent: QWidget | None = None) -> None:
        """Build the dialog.

        Args:
            title: The dialog's window title (e.g. "موافقة على الطلب").
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(360)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.notes_edit = QPlainTextEdit(self)
        self.notes_edit.setFixedHeight(70)
        form.addRow("ملاحظات", self.notes_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.Ok).setText("تأكيد")
        buttons.button(QDialogButtonBox.Cancel).setText("إلغاء")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def notes(self) -> str | None:
        """The entered notes, or ``None`` if left blank."""
        return self.notes_edit.toPlainText().strip() or None


class LeaveRequestsTab(TablePage):
    """The leave-request workflow list, nested inside :class:`LeavePage`."""

    def __init__(
        self,
        *,
        company_id: int,
        policies_tab: LeavePoliciesTab,
        current_user_id: int | None = None,
        permission_codes: frozenset[str] = frozenset(),
        parent: QWidget | None = None,
    ) -> None:
        """Create the requests tab.

        Args:
            company_id: The company this tab manages leave requests for.
            policies_tab: The sibling policies tab, used to source the
                active-policy list for the submission form.
            current_user_id: The signed-in user, for audit attribution.
            permission_codes: The signed-in user's granted permission
                codes.
            parent: Optional parent widget.
        """
        super().__init__(title="طلبات الإجازة", add_button_text="+ تقديم طلب", parent=parent)
        self._controller = LeaveController(
            company_id=company_id,
            actor_user_id=current_user_id,
            permission_codes=permission_codes,
        )
        self._controller.operation_failed.connect(self.show_error)
        self._employee_controller = EmployeeController(
            company_id=company_id,
            actor_user_id=current_user_id,
            permission_codes=permission_codes,
        )
        self._policies_tab = policies_tab

        self.approve_button = _secondary_button("موافقة")
        self.approve_button.clicked.connect(self._on_approve_clicked)
        self.toolbar_layout.addWidget(self.approve_button)

        self.reject_button = _secondary_button("رفض")
        self.reject_button.clicked.connect(self._on_reject_clicked)
        self.toolbar_layout.addWidget(self.reject_button)

        self.cancel_button = _secondary_button("إلغاء الطلب")
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self.toolbar_layout.addWidget(self.cancel_button)

        self.set_columns(
            [
                ("employee_name", "الموظف", lambda row: row.get("employee_name") or ""),
                ("policy_name", "نوع الإجازة", lambda row: row.get("policy_name") or ""),
                (
                    "range",
                    "الفترة",
                    lambda row: f"{row.get('start_date', '')} - {row.get('end_date', '')}",
                ),
                (
                    "days_count",
                    "عدد الأيام",
                    lambda row: str(row.get("days_count") or ""),
                ),
                ("status", "الحالة", lambda row: row.get("status_label_ar") or ""),
                (
                    "reviewed_by_name",
                    "روجعت بواسطة",
                    lambda row: row.get("reviewed_by_name") or "—",
                ),
            ]
        )

        self.add_requested.connect(self._on_add_clicked)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        self.refresh()

    def refresh(self) -> None:
        """Reload the request list from the database."""
        self.clear_error()
        self.populate(self._controller.list_all_requests())
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        """Enable/disable review buttons based on the selected request's status."""
        row = self.selected_row()
        is_pending = row is not None and row.get("status") == "pending"
        self.approve_button.setEnabled(is_pending)
        self.reject_button.setEnabled(is_pending)
        self.cancel_button.setEnabled(is_pending)

    def _on_add_clicked(self) -> None:
        """Open the submission dialog and persist the result if accepted."""
        employees = self._employee_controller.list_employees(active_only=True)
        if not employees:
            self.show_error("لا يوجد موظفون نشطون لتقديم طلب لهم.")
            return
        policies = self._policies_tab.list_active_policies()
        if not policies:
            self.show_error("لا توجد سياسات إجازة نشطة. الرجاء إضافة سياسة أولاً.")
            return
        dialog = LeaveRequestFormDialog(employees=employees, policies=policies, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if values["employee_id"] is None or values["leave_policy_id"] is None:
            return
        if self._controller.submit_request(**values) is not None:
            self.refresh()

    def _on_approve_clicked(self) -> None:
        """Prompt for notes and approve the selected pending request."""
        row = self.selected_row()
        if row is None:
            return
        dialog = ReviewNotesDialog(title="موافقة على طلب الإجازة", parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        if self._controller.approve_request(row["id"], notes=dialog.notes()) is not None:
            self.refresh()

    def _on_reject_clicked(self) -> None:
        """Prompt for notes and reject the selected pending request."""
        row = self.selected_row()
        if row is None:
            return
        dialog = ReviewNotesDialog(title="رفض طلب الإجازة", parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        if self._controller.reject_request(row["id"], notes=dialog.notes()) is not None:
            self.refresh()

    def _on_cancel_clicked(self) -> None:
        """Confirm and cancel the selected pending request."""
        row = self.selected_row()
        if row is None:
            return
        confirmed = ConfirmDialog.confirm(
            self,
            "تأكيد إلغاء الطلب",
            f"هل أنت متأكد من إلغاء طلب إجازة \"{row['employee_name']}\"؟",
            danger=True,
        )
        if not confirmed:
            return
        if self._controller.cancel_request(row["id"]) is not None:
            self.refresh()


# ----------------------------------------------------------------------
# Page
# ----------------------------------------------------------------------


class LeavePage(QTabWidget):
    """The leave policies + leave requests management screen."""

    def __init__(
        self,
        *,
        company_id: int,
        current_user_id: int | None = None,
        permission_codes: frozenset[str] = frozenset(),
        parent: QWidget | None = None,
    ) -> None:
        """Create the leave page.

        Args:
            company_id: The company this screen manages leave for.
            current_user_id: The signed-in user, for audit attribution.
            permission_codes: The signed-in user's granted permission
                codes.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.policies_tab = LeavePoliciesTab(
            company_id=company_id,
            current_user_id=current_user_id,
            permission_codes=permission_codes,
            parent=self,
        )
        self.requests_tab = LeaveRequestsTab(
            company_id=company_id,
            policies_tab=self.policies_tab,
            current_user_id=current_user_id,
            permission_codes=permission_codes,
            parent=self,
        )
        self.addTab(self.requests_tab, "طلبات الإجازة")
        self.addTab(self.policies_tab, "سياسات الإجازات")


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

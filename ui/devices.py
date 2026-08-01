"""Devices screen: manage biometric attendance devices."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from controllers.device_controller import DeviceController
from controllers.employee_controller import EmployeeController
from models.enums import DeviceProtocol
from ui.table_page import TablePage
from ui.widgets import ConfirmDialog, make_danger_button

_PROTOCOL_LABELS_AR = {
    DeviceProtocol.ZKTECO_TCP: "ZKTeco (TCP/IP)",
    DeviceProtocol.ZKTECO_UDP: "ZKTeco (UDP)",
    DeviceProtocol.HIKVISION: "Hikvision",
}


class DeviceFormDialog(QDialog):
    """Add/edit form for a single device."""

    def __init__(
        self,
        *,
        existing: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Build the form dialog.

        Args:
            existing: The device being edited, or ``None`` to create a
                new one.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("تعديل جهاز" if existing else "إضافة جهاز جديد")
        self.setMinimumWidth(400)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.name_edit = QLineEdit(self)
        form.addRow("اسم الجهاز *", self.name_edit)

        self.protocol_combo = QComboBox(self)
        for protocol, label in _PROTOCOL_LABELS_AR.items():
            self.protocol_combo.addItem(label, userData=protocol)
        self.protocol_combo.setEnabled(existing is None)
        form.addRow("البروتوكول *", self.protocol_combo)

        self.host_edit = QLineEdit(self)
        self.host_edit.setPlaceholderText("192.168.1.201")
        form.addRow("عنوان الجهاز (IP) *", self.host_edit)

        self.port_spin = QSpinBox(self)
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(4370)
        form.addRow("المنفذ *", self.port_spin)

        self.communication_key_edit = QLineEdit(self)
        self.communication_key_edit.setPlaceholderText(
            "كلمة اتصال ZKTeco، أو اسم المستخدم:كلمة المرور لأجهزة Hikvision"
        )
        form.addRow("مفتاح الاتصال", self.communication_key_edit)

        self.timeout_spin = QSpinBox(self)
        self.timeout_spin.setRange(1, 120)
        self.timeout_spin.setValue(8)
        self.timeout_spin.setSuffix(" ثانية")
        form.addRow("مهلة الاتصال", self.timeout_spin)

        self.notes_edit = QPlainTextEdit(self)
        self.notes_edit.setFixedHeight(60)
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
        """Pre-fill every field from an existing device's data."""
        self.name_edit.setText(existing.get("name") or "")
        protocol_value = existing.get("protocol")
        if protocol_value is not None:
            index = self.protocol_combo.findData(DeviceProtocol(protocol_value))
            if index >= 0:
                self.protocol_combo.setCurrentIndex(index)
        self.host_edit.setText(existing.get("host") or "")
        if existing.get("port"):
            self.port_spin.setValue(int(existing["port"]))
        if existing.get("timeout_seconds"):
            self.timeout_spin.setValue(int(existing["timeout_seconds"]))
        self.notes_edit.setPlainText(existing.get("notes") or "")
        # communication_key is intentionally never sent back from the
        # controller (see DeviceController._device_to_dict), so it is
        # left blank here too - leaving it blank on save means "keep
        # the existing key unchanged" (see DevicesPage._on_edit_row).

    def values(self) -> dict[str, Any]:
        """Read the form's current state as keyword arguments for the controller.

        Returns:
            A dict compatible with
            :meth:`~controllers.device_controller.DeviceController.create_device`
            / ``update_device``.
        """
        return {
            "name": self.name_edit.text().strip(),
            # Qt's QVariant round-trip degrades a BilingualEnum member
            # (a str subclass) back into a plain str, so it must be
            # reconstructed into a real DeviceProtocol here rather than
            # trusting currentData()'s type - the service layer expects
            # an actual enum instance, not its string value.
            "protocol": DeviceProtocol(self.protocol_combo.currentData()),
            "host": self.host_edit.text().strip(),
            "port": self.port_spin.value(),
            "communication_key": self.communication_key_edit.text().strip() or None,
            "timeout_seconds": self.timeout_spin.value(),
            "notes": self.notes_edit.toPlainText().strip() or None,
        }


class PushEmployeeDialog(QDialog):
    """A focused dialog for picking which employee to push to a device."""

    def __init__(self, *, employees: list[dict[str, Any]], parent: QWidget | None = None) -> None:
        """Build the dialog.

        Args:
            employees: Employees available to enroll.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("دفع موظف إلى الجهاز")
        self.setMinimumWidth(360)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.employee_combo = QComboBox(self)
        for employee in employees:
            label = f"{employee['full_name']} ({employee['employee_number']})"
            self.employee_combo.addItem(label, userData=employee["id"])
        form.addRow("الموظف", self.employee_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.Ok).setText("دفع")
        buttons.button(QDialogButtonBox.Cancel).setText("إلغاء")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def selected_employee_id(self) -> int | None:
        """The chosen employee's id, or ``None`` if the list was empty."""
        return self.employee_combo.currentData()


class DevicesPage(TablePage):
    """The biometric devices management screen."""

    def __init__(self, *, company_id: int, parent: QWidget | None = None) -> None:
        """Create the devices page.

        Args:
            company_id: The company this screen manages devices for.
            parent: Optional parent widget.
        """
        super().__init__(
            title="الأجهزة",
            add_button_text="+ إضافة جهاز",
            search_placeholder="بحث بالاسم...",
            parent=parent,
        )
        self._company_id = company_id
        self._controller = DeviceController(company_id=company_id)
        self._controller.operation_failed.connect(self.show_error)
        self._employee_controller = EmployeeController(company_id=company_id)

        self.test_button = _toolbar_button("اختبار الاتصال")
        self.test_button.clicked.connect(self._on_test_clicked)
        self.toolbar_layout.addWidget(self.test_button)

        self.sync_button = _toolbar_button("مزامنة السجلات")
        self.sync_button.clicked.connect(self._on_sync_clicked)
        self.toolbar_layout.addWidget(self.sync_button)

        self.push_button = _toolbar_button("دفع موظف")
        self.push_button.clicked.connect(self._on_push_clicked)
        self.toolbar_layout.addWidget(self.push_button)

        self.delete_button = make_danger_button("حذف", parent=self)
        self.delete_button.clicked.connect(self._on_delete_clicked)
        self.toolbar_layout.addWidget(self.delete_button)

        self.set_columns(
            [
                ("name", "الاسم", lambda row: row.get("name") or ""),
                (
                    "protocol",
                    "البروتوكول",
                    lambda row: _PROTOCOL_LABELS_AR.get(
                        DeviceProtocol(row["protocol"]), row.get("protocol_label_ar", "")
                    ),
                ),
                (
                    "address",
                    "العنوان",
                    lambda row: f"{row.get('host', '')}:{row.get('port', '')}",
                ),
                ("status_label_ar", "الحالة", lambda row: row.get("status_label_ar") or ""),
                ("notes", "ملاحظات", lambda row: row.get("notes") or "—"),
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
        """Reload the device list from the database."""
        self.clear_error()
        rows = self._controller.list_devices()
        self.populate(rows)
        self._on_selection_changed()

    def _on_search_changed(self, query: str) -> None:
        """Filter the currently loaded devices by name (client-side).

        Device search has no dedicated repository method (unlike
        employee name search), and the device list per company is
        small enough that filtering the already-fetched list is
        simpler than adding one.
        """
        self.clear_error()
        rows = self._controller.list_devices()
        if query.strip():
            needle = query.strip().lower()
            rows = [row for row in rows if needle in (row.get("name") or "").lower()]
        self.populate(rows)
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        """Enable/disable selection-dependent buttons based on the current selection."""
        has_selection = self.selected_row() is not None
        self.test_button.setEnabled(has_selection)
        self.sync_button.setEnabled(has_selection)
        self.push_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)

    # ------------------------------------------------------------------
    # Add / edit / delete
    # ------------------------------------------------------------------

    def _on_add_clicked(self) -> None:
        """Open the "add device" dialog and persist the result if accepted."""
        dialog = DeviceFormDialog(parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["name"] or not values["host"]:
            self.show_error("اسم الجهاز وعنوانه حقلان إلزاميان.")
            return
        result = self._controller.create_device(**values)
        if result is not None:
            self.refresh()

    def _on_edit_row(self, row: dict[str, Any]) -> None:
        """Open the "edit device" dialog for ``row`` and persist changes.

        The protocol cannot be changed once a device exists (matching
        ``DeviceService.update_device``'s documented editable-field
        list, which excludes ``protocol``); an empty communication key
        on save means "leave it unchanged", since the controller never
        returns the real key for display.
        """
        dialog = DeviceFormDialog(existing=row, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["name"] or not values["host"]:
            self.show_error("اسم الجهاز وعنوانه حقلان إلزاميان.")
            return
        values.pop("protocol", None)
        if not values["communication_key"]:
            values.pop("communication_key", None)
        result = self._controller.update_device(row["id"], **values)
        if result is not None:
            self.refresh()

    def _on_delete_clicked(self) -> None:
        """Confirm and delete the currently selected device."""
        row = self.selected_row()
        if row is None:
            return
        confirmed = ConfirmDialog.confirm(
            self,
            "تأكيد حذف الجهاز",
            f"هل أنت متأكد من حذف الجهاز \"{row['name']}\"؟",
            danger=True,
        )
        if not confirmed:
            return
        if self._controller.delete_device(row["id"]):
            self.refresh()

    # ------------------------------------------------------------------
    # Device operations
    # ------------------------------------------------------------------

    def _on_test_clicked(self) -> None:
        """Test connectivity to the selected device."""
        row = self.selected_row()
        if row is None:
            return
        self._controller.test_connection(row["id"])
        self.refresh()

    def _on_sync_clicked(self) -> None:
        """Sync the selected device's attendance logs."""
        row = self.selected_row()
        if row is None:
            return
        self._controller.sync_attendance_logs(row["id"])
        self.refresh()

    def _on_push_clicked(self) -> None:
        """Open the employee picker and push the chosen employee to the selected device."""
        row = self.selected_row()
        if row is None:
            return
        employees = self._employee_controller.list_employees(active_only=True)
        if not employees:
            self.show_error("لا يوجد موظفون نشطون لدفعهم إلى الجهاز.")
            return
        dialog = PushEmployeeDialog(employees=employees, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        employee_id = dialog.selected_employee_id()
        if employee_id is None:
            return
        self._controller.push_employee_to_device(device_id=row["id"], employee_id=employee_id)


def _toolbar_button(text: str) -> QPushButton:
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

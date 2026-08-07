"""Devices screen: manage biometric attendance devices."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config import get_config
from controllers.branch_controller import BranchController
from controllers.device_controller import DeviceController, _connection_test_result_to_dict
from controllers.employee_controller import EmployeeController
from devices.device_manager import DeviceManager
from models.device import Device
from models.enums import DeviceProtocol
from tools.diagnostics import deli_es172_diagnose as network_diagnostic
from ui.table_page import TablePage
from ui.widgets import (
    ConfirmDialog,
    make_danger_button,
    make_primary_button,
    make_secondary_label,
    run_blocking_operation,
)

_PROTOCOL_LABELS_AR = {
    DeviceProtocol.ZKTECO_TCP: "ZKTeco (TCP/IP)",
    DeviceProtocol.ZKTECO_UDP: "ZKTeco (UDP)",
    DeviceProtocol.HIKVISION: "Hikvision",
}


class ConnectionDiagnosticDialog(QDialog):
    """Shows a :class:`~devices.device_interface.ConnectionTestResult` in detail.

    Displays the precise Arabic message, device info on success
    (model/serial/firmware/user count/log count/fingerprint/face
    capability), and a copyable raw technical detail block.
    """

    def __init__(self, *, result: dict[str, Any], parent: QWidget | None = None) -> None:
        """Build the dialog from a controller-serialized diagnostic result.

        Args:
            result: The dict returned by
                :meth:`~controllers.device_controller.DeviceController.test_connection`.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("نتيجة اختبار الاتصال")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(12)

        message_label = make_secondary_label(result.get("message_ar") or "")
        message_label.setProperty("status", "success" if result.get("success") else "danger")
        message_label.setWordWrap(True)
        layout.addWidget(message_label)

        capabilities = result.get("capabilities")
        if capabilities:
            info_lines = []
            if capabilities.get("device_model"):
                info_lines.append(f"الطراز: {capabilities['device_model']}")
            if capabilities.get("serial_number"):
                info_lines.append(f"الرقم التسلسلي: {capabilities['serial_number']}")
            if capabilities.get("firmware_version"):
                info_lines.append(f"إصدار البرنامج الثابت: {capabilities['firmware_version']}")
            if capabilities.get("user_count") is not None:
                info_lines.append(f"عدد المستخدمين: {capabilities['user_count']}")
            if capabilities.get("attendance_log_count") is not None:
                info_lines.append(f"عدد سجلات الحضور: {capabilities['attendance_log_count']}")
            info_lines.append(
                "يدعم البصمة: " + ("نعم" if capabilities.get("supports_fingerprint") else "لا")
            )
            info_lines.append(
                "يدعم بصمة الوجه: " + ("نعم" if capabilities.get("supports_face") else "لا")
            )
            layout.addWidget(make_secondary_label("\n".join(info_lines)))

        detail = result.get("detail")
        if detail:
            layout.addWidget(make_secondary_label("تفاصيل تقنية (قابلة للنسخ):"))
            detail_edit = QTextEdit(self)
            detail_edit.setPlainText(str(detail))
            detail_edit.setReadOnly(True)
            detail_edit.setFixedHeight(80)
            layout.addWidget(detail_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok, parent=self)
        buttons.button(QDialogButtonBox.Ok).setText("موافق")
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


def _format_network_diagnostic_summary(report: dict[str, Any]) -> str:
    """Render a :func:`tools.diagnostics.deli_es172_diagnose.diagnose` report for display.

    Plain-language Arabic labels over the report's technical findings --
    open/closed per port, and for open ports, whatever the safe probing
    could tell (unsolicited banner, plain HTTP, WebSocket upgrade
    accepted, TLS handshake ok). This never claims to identify the
    protocol; it only surfaces what was actually observed, so the
    reader can send it back for the next diagnosis step.
    """
    lines: list[str] = [
        f"عنوان الجهاز: {report['target_ip']}",
        "استجابة ping: " + ("نعم، الجهاز متصل بالشبكة" if report["ping"]["reachable"] else "لا استجابة"),
        "",
    ]
    for entry in report["ports"]:
        if not entry["open"]:
            lines.append(f"المنفذ {entry['port']}: مغلق أو غير متاح")
            continue
        lines.append(f"المنفذ {entry['port']}: مفتوح")
        banner_bytes = entry.get("unsolicited_banner_bytes") or 0
        if banner_bytes:
            lines.append(f"  - أرسل الجهاز بيانات فور الاتصال ({banner_bytes} بايت)")
        if any(probe.get("looks_like_http") for probe in entry.get("http_probes", [])):
            lines.append("  - يستجيب كخادم HTTP عادي")
        websocket_probe = entry.get("websocket_probe")
        if websocket_probe and websocket_probe.get("is_101_switching_protocols"):
            lines.append("  - يقبل ترقية الاتصال إلى WebSocket")
        tls = entry.get("tls")
        if tls and tls.get("tls_handshake_ok"):
            lines.append("  - يدعم تشفير TLS على هذا المنفذ")
        lines.append("")
    return "\n".join(lines).strip()


class NetworkDiagnosticDialog(QDialog):
    """Safe, protocol-agnostic network diagnostic for a device with no connector yet.

    Runs :func:`tools.diagnostics.deli_es172_diagnose.diagnose` directly
    inside the already-installed application -- unlike the standalone
    script it shares its logic with, this needs no separately installed
    Python interpreter, since it is bundled into this same executable.
    Built for investigating a DELI ES172 device, but the underlying
    probing is generic TCP/HTTP/WebSocket fingerprinting safe against
    any device, so the IP field is free-form.
    """

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("تشخيص شبكة الجهاز")
        self.setMinimumWidth(480)
        self._last_output_dir: Path | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(12)

        intro = make_secondary_label(
            "لفحص جهاز غير مدعوم حالياً على شبكتك (مثل بعض أجهزة DELI): أدخل عنوان "
            "IP الخاص به كما يظهر على شاشة الجهاز نفسه، ثم اضغط \"بدء الفحص\". هذا "
            "الفحص آمن تماماً؛ يتحقق فقط من استجابة الجهاز على الشبكة ولا يرسل أي "
            "كلمة مرور أو مفتاح اتصال أو أمر تسجيل."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.ip_edit = QLineEdit(self)
        self.ip_edit.setText(network_diagnostic.DEFAULT_TARGET_IP)
        form.addRow("عنوان الجهاز (IP)", self.ip_edit)
        layout.addLayout(form)

        self.run_button = make_primary_button("بدء الفحص", parent=self)
        self.run_button.clicked.connect(self._on_run_clicked)
        layout.addWidget(self.run_button)

        self.result_view = QTextEdit(self)
        self.result_view.setReadOnly(True)
        self.result_view.setPlaceholderText("ستظهر نتيجة الفحص هنا بعد التشغيل...")
        self.result_view.setMinimumHeight(220)
        layout.addWidget(self.result_view)

        self.open_folder_button = QPushButton("فتح مجلد التقرير", self)
        self.open_folder_button.setEnabled(False)
        self.open_folder_button.clicked.connect(self._on_open_folder_clicked)
        layout.addWidget(self.open_folder_button)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        buttons.button(QDialogButtonBox.Close).setText("إغلاق")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_run_clicked(self) -> None:
        """Run the diagnostic against the entered IP and display + save the result."""
        ip = self.ip_edit.text().strip()
        if not ip:
            QMessageBox.warning(self, "بيانات ناقصة", "الرجاء إدخال عنوان IP للجهاز.")
            return
        self.run_button.setEnabled(False)
        try:
            report = run_blocking_operation(
                self, f"جارٍ فحص الشبكة على {ip} ...", lambda: network_diagnostic.diagnose(ip)
            )
            output_dir = get_config().paths.data_dir / "diagnostics"
            json_path, txt_path = network_diagnostic._write_reports(report, output_dir=output_dir)
            self._last_output_dir = output_dir
            self.open_folder_button.setEnabled(True)
            summary = _format_network_diagnostic_summary(report)
            self.result_view.setPlainText(
                f"{summary}\n\nتم حفظ تقرير كامل قابل للإرسال في:\n{json_path.name}\n{txt_path.name}"
            )
        finally:
            self.run_button.setEnabled(True)

    def _on_open_folder_clicked(self) -> None:
        """Open the folder containing the saved diagnostic reports."""
        if self._last_output_dir is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_output_dir)))


def _probe_connection(values: dict[str, Any]) -> dict[str, Any]:
    """Test a device configuration before it has been saved.

    Builds a throwaway, never-persisted :class:`~models.device.Device`
    from the form's current values and runs the same diagnostic
    connection test the saved-device "اختبار الاتصال" button uses.

    Returns:
        The same dict shape :class:`ConnectionDiagnosticDialog` expects.
    """
    probe = Device(
        name=values["name"] or "probe",
        protocol=values["protocol"],
        host=values["host"],
        port=values["port"],
        communication_key=values["communication_key"],
        timeout_seconds=values.get("timeout_seconds"),
    )
    connector = DeviceManager().get_connector(probe)
    try:
        result = connector.test_connection_detailed()
    finally:
        connector.disconnect()
    return _connection_test_result_to_dict(result)


class DeviceFormDialog(QDialog):
    """Add/edit form for a single device."""

    def __init__(
        self,
        *,
        branches: list[dict[str, Any]] | None = None,
        existing: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Build the form dialog.

        Args:
            branches: Available branches (``{"id", "name", ...}`` dicts)
                to populate the branch picker; omit or pass an empty
                list if this company has none defined yet.
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

        host_hint = make_secondary_label(
            "يظهر هذا العنوان على شاشة جهاز البصمة نفسه: القائمة ← الاتصال ← الشبكة/Ethernet."
        )
        host_hint.setWordWrap(True)
        form.addRow("", host_hint)

        self.branch_combo = QComboBox(self)
        self.branch_combo.addItem("بدون فرع", userData=None)
        for branch in branches or []:
            self.branch_combo.addItem(branch["name"], userData=branch["id"])
        form.addRow("الفرع", self.branch_combo)

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

        self._last_test_succeeded = False

        test_button = make_primary_button("اختبار الاتصال", parent=self)
        test_button.clicked.connect(self._on_test_connection_clicked)
        form.addRow(test_button)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.Save).setText("حفظ")
        buttons.button(QDialogButtonBox.Cancel).setText("إلغاء")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _on_test_connection_clicked(self) -> None:
        """Test the form's current (unsaved) configuration and show the result."""
        values = self.values()
        if not values["name"] or not values["host"]:
            QMessageBox.warning(self, "بيانات ناقصة", "الرجاء إدخال اسم الجهاز وعنوانه أولاً.")
            return
        result = run_blocking_operation(
            self, "جارٍ اختبار الاتصال بالجهاز...", lambda: _probe_connection(values)
        )
        self._last_test_succeeded = bool(result.get("success"))
        ConnectionDiagnosticDialog(result=result, parent=self).exec()

    def last_test_succeeded(self) -> bool:
        """Whether the most recent in-dialog connection test succeeded.

        ``False`` both when a test failed and when no test was ever
        run — the caller (``DevicesPage``) treats both the same way:
        confirm before saving an unverified configuration.
        """
        return self._last_test_succeeded

    def _apply_existing(self, existing: dict[str, Any]) -> None:
        """Pre-fill every field from an existing device's data."""
        self.name_edit.setText(existing.get("name") or "")
        protocol_value = existing.get("protocol")
        if protocol_value is not None:
            index = self.protocol_combo.findData(DeviceProtocol(protocol_value))
            if index >= 0:
                self.protocol_combo.setCurrentIndex(index)
        self.host_edit.setText(existing.get("host") or "")
        branch_id = existing.get("branch_id")
        branch_index = self.branch_combo.findData(branch_id)
        self.branch_combo.setCurrentIndex(branch_index if branch_index >= 0 else 0)
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
            "branch_id": self.branch_combo.currentData(),
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

    def __init__(
        self,
        *,
        company_id: int,
        current_user_id: int | None = None,
        permission_codes: frozenset[str] = frozenset(),
        parent: QWidget | None = None,
    ) -> None:
        """Create the devices page.

        Args:
            company_id: The company this screen manages devices for.
            current_user_id: The signed-in user, for audit attribution.
            permission_codes: The signed-in user's granted permission
                codes.
            parent: Optional parent widget.
        """
        super().__init__(
            title="الأجهزة",
            add_button_text="+ إضافة جهاز",
            search_placeholder="بحث بالاسم...",
            parent=parent,
        )
        self._company_id = company_id
        self._controller = DeviceController(
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
        self._branch_controller = BranchController(
            company_id=company_id,
            actor_user_id=current_user_id,
            permission_codes=permission_codes,
        )

        self.test_button = _toolbar_button("اختبار الاتصال")
        self.test_button.clicked.connect(self._on_test_clicked)
        self.toolbar_layout.addWidget(self.test_button)

        self.sync_button = _toolbar_button("مزامنة السجلات")
        self.sync_button.clicked.connect(self._on_sync_clicked)
        self.toolbar_layout.addWidget(self.sync_button)

        self.push_button = _toolbar_button("دفع موظف")
        self.push_button.clicked.connect(self._on_push_clicked)
        self.toolbar_layout.addWidget(self.push_button)

        self.pull_button = _toolbar_button("تنزيل الموظفين")
        self.pull_button.clicked.connect(self._on_pull_clicked)
        self.toolbar_layout.addWidget(self.pull_button)

        # Always enabled (unlike the buttons above): diagnoses a device by
        # IP directly, independent of the selected row -- for devices with
        # no supported protocol/connector yet (e.g. a DELI ES172), which
        # therefore cannot be added to this list at all.
        self.network_diagnostic_button = _toolbar_button("تشخيص شبكة الجهاز")
        self.network_diagnostic_button.setEnabled(True)
        self.network_diagnostic_button.clicked.connect(self._on_network_diagnostic_clicked)
        self.toolbar_layout.addWidget(self.network_diagnostic_button)

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
        self.pull_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)

    # ------------------------------------------------------------------
    # Add / edit / delete
    # ------------------------------------------------------------------

    def _load_branch_choices(self) -> list[dict[str, Any]]:
        """Fetch every branch for the form's branch picker."""
        return self._branch_controller.list_branches()

    def _on_add_clicked(self) -> None:
        """Open the "add device" dialog and persist the result if accepted."""
        dialog = DeviceFormDialog(branches=self._load_branch_choices(), parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["name"] or not values["host"]:
            self.show_error("اسم الجهاز وعنوانه حقلان إلزاميان.")
            return
        if not dialog.last_test_succeeded():
            confirmed = ConfirmDialog.confirm(
                self,
                "لم يُتحقق من الاتصال بالجهاز",
                "لم يتم اختبار الاتصال بنجاح لهذا الجهاز. هل تريد حفظه كإعداد غير متصل؟ "
                "يمكن اختبار الاتصال والتحقق منه لاحقاً من شاشة الأجهزة.",
                danger=True,
            )
            if not confirmed:
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
        dialog = DeviceFormDialog(
            branches=self._load_branch_choices(), existing=row, parent=self
        )
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
        """Test connectivity to the selected device and show a diagnostic dialog."""
        row = self.selected_row()
        if row is None:
            return
        result = run_blocking_operation(
            self, "جارٍ اختبار الاتصال بالجهاز...", lambda: self._controller.test_connection(row["id"])
        )
        self.refresh()
        if result is not None:
            ConnectionDiagnosticDialog(result=result, parent=self).exec()

    def _on_network_diagnostic_clicked(self) -> None:
        """Open the safe network diagnostic for a device with no connector yet."""
        NetworkDiagnosticDialog(parent=self).exec()

    def _on_sync_clicked(self) -> None:
        """Sync the selected device's attendance logs."""
        row = self.selected_row()
        if row is None:
            return
        new_count = run_blocking_operation(
            self,
            "جارٍ تنزيل سجلات الحضور من الجهاز...",
            lambda: self._controller.sync_attendance_logs(row["id"]),
        )
        self.refresh()
        if new_count > 0:
            QMessageBox.information(
                self,
                "مزامنة السجلات",
                f"تم تنزيل {new_count} سجل حضور جديد من الجهاز.",
            )
        else:
            QMessageBox.information(
                self,
                "مزامنة السجلات",
                "لا توجد سجلات حضور جديدة على الجهاز.",
            )

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
        selected_employee = next(
            (employee for employee in employees if employee["id"] == employee_id), None
        )
        succeeded = run_blocking_operation(
            self,
            "جارٍ إرسال بيانات الموظف إلى الجهاز...",
            lambda: self._controller.push_employee_to_device(
                device_id=row["id"], employee_id=employee_id
            ),
        )
        if succeeded and selected_employee is not None:
            QMessageBox.information(
                self,
                "إرسال إلى الجهاز",
                f"تم إرسال بيانات الموظف \"{selected_employee['full_name']}\" إلى الجهاز بنجاح.",
            )

    def _on_pull_clicked(self) -> None:
        """Download the selected device's enrolled users and import unmatched ones as employees."""
        row = self.selected_row()
        if row is None:
            return
        created_count = run_blocking_operation(
            self,
            "جارٍ تنزيل الموظفين من الجهاز...",
            lambda: self._controller.pull_employees_from_device(row["id"]),
        )
        self.refresh()
        if created_count > 0:
            QMessageBox.information(
                self,
                "تنزيل الموظفين",
                f"تم استيراد {created_count} موظف جديد من الجهاز.",
            )
        else:
            QMessageBox.information(
                self,
                "تنزيل الموظفين",
                "لا يوجد موظفون جدد لاستيرادهم من الجهاز.",
            )


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

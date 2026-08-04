"""Configuration publishing panel: publish, compare, view history, roll back.

Talks to :class:`~developer_suite.services.configuration_publish_service.ConfigurationPublishService`
for every publish/compare/rollback/history operation, and to
:class:`~developer_suite.services.configuration_service.ConfigurationService`/
:class:`~developer_suite.services.customer_service.CustomerService`/
:class:`~developer_suite.admin.client.AdminApiClient` only to populate
its three pickers (bundle, customer, target installation) — the same
service/UI boundary every other Developer Suite page already follows.
Device listing is a synchronous call here, exactly like
:mod:`developer_suite.ui.monitoring_page`'s own established pattern
for the same :class:`~developer_suite.admin.client.AdminApiClient`
call.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from developer_suite.admin.client import AdminApiClient, AdminApiError
from developer_suite.admin.session_manager import AdminSessionManager
from developer_suite.models.configuration_publication import ConfigurationPublication
from developer_suite.services.configuration_publish_service import (
    ConfigurationPublishService,
    ConfigurationPublishServiceError,
)
from developer_suite.services.configuration_service import ConfigurationService
from developer_suite.services.customer_service import CustomerService

_HISTORY_COLUMNS = ("الإصدار", "تاريخ النشر", "بواسطة", "ملخص التغيير")


class _CompareDialog(QDialog):
    """Read-only view of the pending-changes diff between a draft bundle and its last publish."""

    def __init__(self, differences: dict[str, tuple[object, object]], *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("مقارنة التغييرات المعلّقة")
        self.setMinimumSize(560, 360)

        layout = QVBoxLayout(self)
        if not differences:
            layout.addWidget(QLabel("لا توجد تغييرات معلّقة عن آخر إصدار منشور.", self))
        else:
            table = QTableWidget(len(differences), 3, self)
            table.setHorizontalHeaderLabels(("الحقل", "المنشور حالياً", "المسودة الحالية"))
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for row, (field, (old_value, new_value)) in enumerate(differences.items()):
                table.setItem(row, 0, QTableWidgetItem(field))
                table.setItem(row, 1, QTableWidgetItem("—" if old_value is None else str(old_value)))
                table.setItem(row, 2, QTableWidgetItem("—" if new_value is None else str(new_value)))
            layout.addWidget(table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class ConfigurationPublishPanel(QWidget):
    """Pick a bundle/customer/installation, then publish, compare, or roll back."""

    def __init__(
        self,
        configuration_service: ConfigurationService,
        publish_service: ConfigurationPublishService,
        customer_service: CustomerService,
        admin_client: AdminApiClient,
        admin_session_manager: AdminSessionManager,
        *,
        parent: QWidget | None = None,
    ) -> None:
        """Build the publishing panel.

        Args:
            configuration_service: Populates the configuration bundle
                picker.
            publish_service: Performs every publish/compare/rollback
                operation.
            customer_service: Populates the customer picker.
            admin_client: Populates the target-installation picker
                from the Attendance Server's registered devices.
            admin_session_manager: Supplies the current administrator's
                username for every publish/rollback call.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._configuration_service = configuration_service
        self._publish_service = publish_service
        self._customer_service = customer_service
        self._admin_client = admin_client
        self._admin_session_manager = admin_session_manager

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        picker_box = QGroupBox("الهدف", self)
        picker_form = QFormLayout(picker_box)

        self.bundle_combo = QComboBox(picker_box)
        picker_form.addRow("حزمة الإعدادات", self.bundle_combo)

        self.customer_combo = QComboBox(picker_box)
        picker_form.addRow("العميل", self.customer_combo)

        device_row = QHBoxLayout()
        self.device_combo = QComboBox(picker_box)
        device_row.addWidget(self.device_combo, stretch=1)
        self.refresh_devices_button = QPushButton("تحديث الأجهزة", picker_box)
        self.refresh_devices_button.clicked.connect(self._reload_devices)
        device_row.addWidget(self.refresh_devices_button)
        picker_form.addRow("التثبيت المستهدف", device_row)

        self.device_status_label = QLabel("", picker_box)
        picker_form.addRow("", self.device_status_label)

        layout.addWidget(picker_box)

        status_box = QGroupBox("حالة الإصدار", self)
        status_layout = QVBoxLayout(status_box)
        self.current_version_label = QLabel("لم يتم النشر إلى هذا الجهاز بعد.", status_box)
        status_layout.addWidget(self.current_version_label)
        layout.addWidget(status_box)

        publish_box = QGroupBox("نشر", self)
        publish_layout = QVBoxLayout(publish_box)
        self.change_summary_edit = QLineEdit(publish_box)
        self.change_summary_edit.setPlaceholderText("ملخص التغيير (اختياري)")
        publish_layout.addWidget(self.change_summary_edit)

        actions_row = QHBoxLayout()
        self.compare_button = QPushButton("مقارنة التغييرات المعلّقة", publish_box)
        self.compare_button.clicked.connect(self._on_compare_clicked)
        actions_row.addWidget(self.compare_button)
        self.publish_button = QPushButton("نشر", publish_box)
        self.publish_button.clicked.connect(self._on_publish_clicked)
        actions_row.addWidget(self.publish_button)
        publish_layout.addLayout(actions_row)
        layout.addWidget(publish_box)

        history_box = QGroupBox("سجل الإصدارات", self)
        history_layout = QVBoxLayout(history_box)
        self.history_table = QTableWidget(0, len(_HISTORY_COLUMNS), history_box)
        self.history_table.setHorizontalHeaderLabels(_HISTORY_COLUMNS)
        self.history_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.history_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        history_layout.addWidget(self.history_table)
        self.rollback_button = QPushButton("التراجع إلى الإصدار المحدد", history_box)
        self.rollback_button.clicked.connect(self._on_rollback_clicked)
        history_layout.addWidget(self.rollback_button)
        layout.addWidget(history_box)

        self._history: list[ConfigurationPublication] = []

        self.bundle_combo.currentIndexChanged.connect(self._reload_status)
        self.customer_combo.currentIndexChanged.connect(self._reload_status)
        self.device_combo.currentIndexChanged.connect(self._reload_status)

        self.reload()

    def reload(self) -> None:
        """Reload every picker and the status/history panels."""
        self._reload_bundles()
        self._reload_customers()
        self._reload_devices()

    def _reload_bundles(self) -> None:
        self.bundle_combo.blockSignals(True)
        self.bundle_combo.clear()
        for bundle in self._configuration_service.list_configurations():
            self.bundle_combo.addItem(f"{bundle.name} (v{bundle.version})", userData=bundle.id)
        self.bundle_combo.blockSignals(False)

    def _reload_customers(self) -> None:
        self.customer_combo.blockSignals(True)
        self.customer_combo.clear()
        for customer in self._customer_service.search_customers():
            self.customer_combo.addItem(customer.company_name, userData=customer.id)
        self.customer_combo.blockSignals(False)

    def _reload_devices(self) -> None:
        """Reload the target-installation picker from the Attendance Server.

        Reports a failure (no admin token configured yet, or the
        server is unreachable) through :attr:`device_status_label`,
        never a blocking dialog — this runs on every panel load, not
        only in response to an explicit user action, so popping a
        modal here would surprise the user (or, in a no-display test
        environment, hang forever waiting for a dismissal that will
        never come), the same inline-label convention
        :meth:`~developer_suite.ui.monitoring_page.MonitoringPage.reload`
        already established for the same
        :class:`~developer_suite.admin.client.AdminApiClient` failure
        modes.
        """
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        try:
            devices = self._admin_client.list_devices()
        except AdminApiError as exc:
            self.device_status_label.setText(f"تعذّر تحميل الأجهزة: {exc}")
            self.device_combo.blockSignals(False)
            self._reload_status()
            return
        self.device_status_label.setText("")
        for device in devices:
            if device.device_type != "attendance_client":
                continue
            self.device_combo.addItem(device.name, userData=device.public_id)
        self.device_combo.blockSignals(False)
        self._reload_status()

    def _selected_bundle_id(self) -> int | None:
        return self.bundle_combo.currentData()

    def _selected_customer_id(self) -> int | None:
        return self.customer_combo.currentData()

    def _selected_device_public_id(self) -> str | None:
        return self.device_combo.currentData()

    def _reload_status(self) -> None:
        target = self._selected_device_public_id()
        if target is None:
            self.current_version_label.setText("لا يوجد جهاز مستهدف محدد.")
            self._set_history([])
            return

        current = self._publish_service.get_current_publication(target)
        if current is None:
            self.current_version_label.setText("لم يتم النشر إلى هذا الجهاز بعد.")
        else:
            self.current_version_label.setText(
                f"الإصدار الحالي: {current.version} — نُشر بواسطة {current.published_by} "
                f"في {current.created_at.strftime('%Y-%m-%d %H:%M')}"
            )
        self._set_history(self._publish_service.list_publication_history(target))

    def _set_history(self, history: list[ConfigurationPublication]) -> None:
        self._history = history
        self.history_table.setRowCount(len(history))
        for row, publication in enumerate(history):
            self.history_table.setItem(row, 0, QTableWidgetItem(str(publication.version)))
            self.history_table.setItem(
                row, 1, QTableWidgetItem(publication.created_at.strftime("%Y-%m-%d %H:%M"))
            )
            self.history_table.setItem(row, 2, QTableWidgetItem(publication.published_by))
            self.history_table.setItem(row, 3, QTableWidgetItem(publication.change_summary or ""))

    def _current_admin_username(self) -> str | None:
        account = self._admin_session_manager.current_account
        return account.username if account is not None else None

    def _on_compare_clicked(self) -> None:
        bundle_id = self._selected_bundle_id()
        customer_id = self._selected_customer_id()
        target = self._selected_device_public_id()
        if bundle_id is None or customer_id is None or target is None:
            QMessageBox.information(self, "مقارنة", "الرجاء اختيار حزمة الإعدادات والعميل والجهاز المستهدف.")
            return
        try:
            differences = self._publish_service.compare_pending_changes(
                bundle_id, customer_id=customer_id, target_device_public_id=target
            )
        except ConfigurationPublishServiceError as exc:
            QMessageBox.warning(self, "تعذّرت المقارنة", str(exc))
            return
        _CompareDialog(differences, parent=self).exec()

    def _on_publish_clicked(self) -> None:
        bundle_id = self._selected_bundle_id()
        customer_id = self._selected_customer_id()
        target = self._selected_device_public_id()
        if bundle_id is None or customer_id is None or target is None:
            QMessageBox.information(self, "نشر", "الرجاء اختيار حزمة الإعدادات والعميل والجهاز المستهدف.")
            return

        username = self._current_admin_username()
        if username is None:
            QMessageBox.warning(self, "تعذّر النشر", "يجب تسجيل الدخول كمسؤول لنشر الإعدادات.")
            return

        confirmed = QMessageBox.question(
            self,
            "تأكيد النشر",
            "هل تريد نشر هذه الإعدادات إلى الجهاز المحدد؟",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return

        try:
            self._publish_service.publish(
                bundle_id,
                customer_id=customer_id,
                target_device_public_id=target,
                published_by=username,
                change_summary=self.change_summary_edit.text().strip() or None,
            )
        except ConfigurationPublishServiceError as exc:
            QMessageBox.warning(self, "تعذّر النشر", str(exc))
            return

        self.change_summary_edit.clear()
        self._reload_status()

    def _on_rollback_clicked(self) -> None:
        target = self._selected_device_public_id()
        if target is None:
            QMessageBox.information(self, "التراجع", "الرجاء اختيار جهاز مستهدف أولاً.")
            return
        row = self.history_table.currentRow()
        if row < 0 or row >= len(self._history):
            QMessageBox.information(self, "التراجع", "الرجاء اختيار إصدار من السجل أولاً.")
            return

        username = self._current_admin_username()
        if username is None:
            QMessageBox.warning(self, "تعذّر التراجع", "يجب تسجيل الدخول كمسؤول للتراجع عن الإعدادات.")
            return

        selected = self._history[row]
        confirmed = QMessageBox.question(
            self,
            "تأكيد التراجع",
            f"هل تريد التراجع إلى الإصدار {selected.version}؟",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return

        try:
            self._publish_service.rollback(
                target, to_publication_id=selected.id, published_by=username
            )
        except ConfigurationPublishServiceError as exc:
            QMessageBox.warning(self, "تعذّر التراجع", str(exc))
            return

        self._reload_status()

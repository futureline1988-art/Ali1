"""Update Manager page: create, sign/upload, target, publish, schedule, disable, and roll back.

Mirrors :mod:`developer_suite.ui.configuration_publish_panel`'s shape
closely (the same service/UI boundary, the same "background reload
failures go to an inline label, never a blocking dialog" discipline,
the same explicit-user-action-only use of :class:`~PySide6.QtWidgets.QMessageBox`)
— this is the second page in the Developer Suite that publishes
something toward customer installations, and it reuses that first
page's proven interaction pattern rather than inventing a new one.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateTimeEdit,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from developer_suite.admin.client import AdminApiClient, AdminApiError
from developer_suite.services.customer_group_service import CustomerGroupService
from developer_suite.services.customer_service import CustomerService
from developer_suite.services.update_manager_service import (
    UpdateManagerService,
    UpdateManagerServiceError,
    UpdateSigningKeyError,
)

_VERSION_COLUMNS = ("الإصدار", "النوع", "الحالة", "تاريخ النشر")
_UPDATE_TYPE_LABELS_AR = {
    "optional": "اختياري",
    "recommended": "موصى به",
    "critical": "حرج",
    "mandatory": "إلزامي",
}
_PUBLISH_STATUS_LABELS_AR = {
    "draft": "مسودة",
    "scheduled": "مجدول",
    "published": "منشور",
    "disabled": "معطّل",
    "rolled_back": "تم التراجع عنه",
}


class UpdateManagerPage(QWidget):
    """Create, sign/upload, target, publish, schedule, disable, and roll back software updates."""

    def __init__(
        self,
        update_manager_service: UpdateManagerService,
        customer_service: CustomerService,
        customer_group_service: CustomerGroupService,
        admin_client: AdminApiClient,
        *,
        parent: QWidget | None = None,
    ) -> None:
        """Build the Update Manager page.

        Args:
            update_manager_service: Performs every create/upload/target/
                publish/schedule/disable/rollback operation.
            customer_service: Populates the "specific customers" picker.
            customer_group_service: Populates the "customer group" picker.
            admin_client: Populates the device-targeting list from the
                Attendance Server's registered devices.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._update_manager_service = update_manager_service
        self._customer_service = customer_service
        self._customer_group_service = customer_group_service
        self._admin_client = admin_client
        self._versions: list = []
        self._selected_version_id: int | None = None
        self._registered_devices: list = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)

        self.status_label = QLabel("", self)
        outer.addWidget(self.status_label)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        outer.addWidget(splitter, stretch=1)

        splitter.addWidget(self._build_versions_panel())
        splitter.addWidget(self._build_detail_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        self.reload()

    # -- Panel construction ---------------------------------------------------

    def _build_versions_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)

        create_box = QGroupBox("إصدار جديد", panel)
        form = QFormLayout(create_box)
        self.new_version_edit = QLineEdit(create_box)
        self.new_version_edit.setPlaceholderText("مثال: 1.2.0")
        form.addRow("رقم الإصدار", self.new_version_edit)
        self.new_min_version_edit = QLineEdit(create_box)
        self.new_min_version_edit.setPlaceholderText("اختياري")
        form.addRow("أقل إصدار مدعوم", self.new_min_version_edit)
        self.new_update_type_combo = QComboBox(create_box)
        for value, label in _UPDATE_TYPE_LABELS_AR.items():
            self.new_update_type_combo.addItem(label, userData=value)
        form.addRow("نوع التحديث", self.new_update_type_combo)
        self.new_release_notes_edit = QTextEdit(create_box)
        self.new_release_notes_edit.setPlaceholderText("ملاحظات الإصدار")
        self.new_release_notes_edit.setMaximumHeight(80)
        form.addRow("ملاحظات الإصدار", self.new_release_notes_edit)
        self.create_version_button = QPushButton("إنشاء إصدار", create_box)
        self.create_version_button.clicked.connect(self._on_create_version_clicked)
        form.addRow("", self.create_version_button)
        layout.addWidget(create_box)

        self.versions_table = QTableWidget(0, len(_VERSION_COLUMNS), panel)
        self.versions_table.setHorizontalHeaderLabels(_VERSION_COLUMNS)
        self.versions_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.versions_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.versions_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.versions_table.itemSelectionChanged.connect(self._on_version_selected)
        layout.addWidget(self.versions_table, stretch=1)

        return panel

    def _build_detail_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)

        self.detail_label = QLabel("اختر إصداراً من القائمة.", panel)
        layout.addWidget(self.detail_label)

        packages_box = QGroupBox("الحزم", panel)
        packages_layout = QVBoxLayout(packages_box)
        self.packages_table = QTableWidget(0, 3, packages_box)
        self.packages_table.setHorizontalHeaderLabels(("النوع", "الحجم (بايت)", "بصمة SHA-256"))
        self.packages_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.packages_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        packages_layout.addWidget(self.packages_table)
        upload_row = QHBoxLayout()
        self.upload_setup_button = QPushButton("رفع حزمة التثبيت (Setup)", packages_box)
        self.upload_setup_button.clicked.connect(lambda: self._on_upload_clicked("setup"))
        upload_row.addWidget(self.upload_setup_button)
        self.upload_portable_button = QPushButton("رفع الحزمة المحمولة (Portable)", packages_box)
        self.upload_portable_button.clicked.connect(lambda: self._on_upload_clicked("portable"))
        upload_row.addWidget(self.upload_portable_button)
        packages_layout.addLayout(upload_row)
        layout.addWidget(packages_box)

        target_box = QGroupBox("الاستهداف", panel)
        target_layout = QVBoxLayout(target_box)
        picker_row = QHBoxLayout()
        self.target_scope_combo = QComboBox(target_box)
        self.target_scope_combo.addItem("كل العملاء", userData="all")
        self.target_scope_combo.addItem("عملاء محددون", userData="customers")
        self.target_scope_combo.addItem("مجموعة عملاء", userData="group")
        self.target_scope_combo.currentIndexChanged.connect(self._on_target_scope_changed)
        picker_row.addWidget(self.target_scope_combo)
        self.target_customer_combo = QComboBox(target_box)
        picker_row.addWidget(self.target_customer_combo)
        self.target_group_combo = QComboBox(target_box)
        picker_row.addWidget(self.target_group_combo)
        self.suggest_devices_button = QPushButton("اقتراح الأجهزة", target_box)
        self.suggest_devices_button.clicked.connect(self._on_suggest_devices_clicked)
        picker_row.addWidget(self.suggest_devices_button)
        target_layout.addLayout(picker_row)

        self.devices_list = QListWidget(target_box)
        self.devices_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        target_layout.addWidget(self.devices_list)

        self.set_targets_button = QPushButton("تعيين الاستهداف", target_box)
        self.set_targets_button.clicked.connect(self._on_set_targets_clicked)
        target_layout.addWidget(self.set_targets_button)
        layout.addWidget(target_box)

        actions_box = QGroupBox("إجراءات النشر", panel)
        actions_layout = QVBoxLayout(actions_box)
        schedule_row = QHBoxLayout()
        self.schedule_datetime_edit = QDateTimeEdit(actions_box)
        self.schedule_datetime_edit.setCalendarPopup(True)
        self.schedule_datetime_edit.setDateTime(datetime.now() + timedelta(hours=1))
        schedule_row.addWidget(self.schedule_datetime_edit)
        self.schedule_button = QPushButton("جدولة", actions_box)
        self.schedule_button.clicked.connect(self._on_schedule_clicked)
        schedule_row.addWidget(self.schedule_button)
        actions_layout.addLayout(schedule_row)

        buttons_row = QHBoxLayout()
        self.publish_button = QPushButton("نشر الآن", actions_box)
        self.publish_button.clicked.connect(self._on_publish_clicked)
        buttons_row.addWidget(self.publish_button)
        self.disable_button = QPushButton("تعطيل", actions_box)
        self.disable_button.clicked.connect(self._on_disable_clicked)
        buttons_row.addWidget(self.disable_button)
        actions_layout.addLayout(buttons_row)

        rollback_row = QHBoxLayout()
        self.rollback_reason_edit = QLineEdit(actions_box)
        self.rollback_reason_edit.setPlaceholderText("سبب التراجع (اختياري)")
        rollback_row.addWidget(self.rollback_reason_edit, stretch=1)
        self.rollback_button = QPushButton("التراجع عن هذا الإصدار", actions_box)
        self.rollback_button.clicked.connect(self._on_rollback_clicked)
        rollback_row.addWidget(self.rollback_button)
        actions_layout.addLayout(rollback_row)
        layout.addWidget(actions_box)

        audit_box = QGroupBox("سجل الإجراءات", panel)
        audit_layout = QVBoxLayout(audit_box)
        self.audit_table = QTableWidget(0, 3, audit_box)
        self.audit_table.setHorizontalHeaderLabels(("الإجراء", "بواسطة", "التاريخ"))
        self.audit_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.audit_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        audit_layout.addWidget(self.audit_table)
        layout.addWidget(audit_box)

        self._on_target_scope_changed()
        return panel

    # -- Reload -----------------------------------------------------------------

    def reload(self) -> None:
        """Reload the version list and every targeting picker."""
        self._reload_versions()
        self._reload_customers()
        self._reload_groups()
        self._reload_devices()

    def _reload_versions(self) -> None:
        try:
            self._versions = self._update_manager_service.list_versions()
        except UpdateManagerServiceError as exc:
            self.status_label.setText(f"تعذّر تحميل الإصدارات: {exc}")
            self._versions = []
        self.versions_table.setRowCount(len(self._versions))
        for row, version in enumerate(self._versions):
            self.versions_table.setItem(row, 0, QTableWidgetItem(version.version))
            self.versions_table.setItem(
                row, 1, QTableWidgetItem(_UPDATE_TYPE_LABELS_AR.get(version.update_type, version.update_type))
            )
            self.versions_table.setItem(
                row, 2, QTableWidgetItem(_PUBLISH_STATUS_LABELS_AR.get(version.publish_status, version.publish_status))
            )
            published = version.published_at.strftime("%Y-%m-%d %H:%M") if version.published_at else "—"
            self.versions_table.setItem(row, 3, QTableWidgetItem(published))
            self.versions_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, version.id)

    def _reload_customers(self) -> None:
        self.target_customer_combo.blockSignals(True)
        self.target_customer_combo.clear()
        for customer in self._customer_service.search_customers():
            self.target_customer_combo.addItem(customer.company_name, userData=customer.id)
        self.target_customer_combo.blockSignals(False)

    def _reload_groups(self) -> None:
        self.target_group_combo.blockSignals(True)
        self.target_group_combo.clear()
        for group in self._customer_group_service.list_groups():
            self.target_group_combo.addItem(group.name, userData=group.id)
        self.target_group_combo.blockSignals(False)

    def _reload_devices(self) -> None:
        """Reload the device-targeting list from the Attendance Server.

        Failures go to :attr:`status_label`, never a blocking dialog —
        this runs automatically on every page load (see
        :mod:`developer_suite.ui.configuration_publish_panel`'s own
        identical reasoning for the exact same
        :class:`~developer_suite.admin.client.AdminApiClient` failure
        modes).
        """
        self.devices_list.clear()
        try:
            devices = self._admin_client.list_devices()
        except AdminApiError as exc:
            self.status_label.setText(f"تعذّر تحميل الأجهزة: {exc}")
            self._registered_devices = []
            return
        self._registered_devices = [d for d in devices if d.device_type == "attendance_client"]
        for device in self._registered_devices:
            item = QListWidgetItem(f"{device.name} ({device.public_id[:8]}…)", self.devices_list)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, device.public_id)

    def _selected_version_id_from_table(self) -> int | None:
        row = self.versions_table.currentRow()
        if row < 0:
            return None
        item = self.versions_table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _on_version_selected(self) -> None:
        self._selected_version_id = self._selected_version_id_from_table()
        self._reload_detail()

    def _reload_detail(self) -> None:
        if self._selected_version_id is None:
            self.detail_label.setText("اختر إصداراً من القائمة.")
            self.packages_table.setRowCount(0)
            self.audit_table.setRowCount(0)
            return
        try:
            detail = self._update_manager_service.get_version_detail(self._selected_version_id)
        except UpdateManagerServiceError as exc:
            self.detail_label.setText(f"تعذّر تحميل التفاصيل: {exc}")
            return

        version = detail["version"]
        self.detail_label.setText(
            f"الإصدار {version.version} — {_PUBLISH_STATUS_LABELS_AR.get(version.publish_status, version.publish_status)}"
        )

        packages = detail["packages"]
        self.packages_table.setRowCount(len(packages))
        for row, package in enumerate(packages):
            self.packages_table.setItem(row, 0, QTableWidgetItem(package["package_type"]))
            self.packages_table.setItem(row, 1, QTableWidgetItem(str(package["size_bytes"])))
            self.packages_table.setItem(row, 2, QTableWidgetItem(package["checksum_sha256"]))

        audit_events = detail["audit_events"]
        self.audit_table.setRowCount(len(audit_events))
        for row, event in enumerate(audit_events):
            self.audit_table.setItem(row, 0, QTableWidgetItem(event["action"]))
            self.audit_table.setItem(row, 1, QTableWidgetItem(event["performed_by"]))
            self.audit_table.setItem(row, 2, QTableWidgetItem(event["created_at"][:16].replace("T", " ")))

    # -- Actions -----------------------------------------------------------------

    def _on_create_version_clicked(self) -> None:
        version = self.new_version_edit.text().strip()
        if not version:
            QMessageBox.information(self, "إصدار جديد", "الرجاء إدخال رقم الإصدار.")
            return
        try:
            self._update_manager_service.create_version(
                version=version,
                release_notes=self.new_release_notes_edit.toPlainText().strip() or None,
                min_supported_version=self.new_min_version_edit.text().strip() or None,
                update_type=self.new_update_type_combo.currentData(),
            )
        except UpdateManagerServiceError as exc:
            QMessageBox.warning(self, "تعذّر الإنشاء", str(exc))
            return
        self.new_version_edit.clear()
        self.new_min_version_edit.clear()
        self.new_release_notes_edit.clear()
        self._reload_versions()

    def _on_upload_clicked(self, package_type: str) -> None:
        if self._selected_version_id is None:
            QMessageBox.information(self, "رفع حزمة", "الرجاء اختيار إصدار أولاً.")
            return
        chosen, _filter = QFileDialog.getOpenFileName(self, "اختيار ملف الحزمة", "", "All Files (*)")
        if not chosen:
            return
        from pathlib import Path

        try:
            self._update_manager_service.upload_package(
                self._selected_version_id, package_type=package_type, file_path=Path(chosen)
            )
        except UpdateSigningKeyError as exc:
            QMessageBox.warning(self, "تعذّر التوقيع", str(exc))
            return
        except UpdateManagerServiceError as exc:
            QMessageBox.warning(self, "تعذّر الرفع", str(exc))
            return
        self._reload_detail()

    def _on_target_scope_changed(self) -> None:
        scope = self.target_scope_combo.currentData()
        self.target_customer_combo.setVisible(scope == "customers")
        self.target_group_combo.setVisible(scope == "group")
        self.suggest_devices_button.setVisible(scope in ("customers", "group"))

    def _on_suggest_devices_clicked(self) -> None:
        scope = self.target_scope_combo.currentData()
        if scope == "customers":
            customer_id = self.target_customer_combo.currentData()
            customers = [c for c in self._customer_service.search_customers() if c.id == customer_id]
        elif scope == "group":
            group_id = self.target_group_combo.currentData()
            group = self._customer_group_service.get_group(group_id) if group_id is not None else None
            customers = list(group.customers) if group is not None else []
        else:
            customers = []
        suggested = set(
            self._update_manager_service.suggest_devices_for_customers(
                customers, registered_devices=self._registered_devices
            )
        )
        for row in range(self.devices_list.count()):
            item = self.devices_list.item(row)
            public_id = item.data(Qt.ItemDataRole.UserRole)
            item.setCheckState(Qt.CheckState.Checked if public_id in suggested else Qt.CheckState.Unchecked)

    def _checked_device_public_ids(self) -> list[str]:
        checked = []
        for row in range(self.devices_list.count()):
            item = self.devices_list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                checked.append(item.data(Qt.ItemDataRole.UserRole))
        return checked

    def _on_set_targets_clicked(self) -> None:
        if self._selected_version_id is None:
            QMessageBox.information(self, "الاستهداف", "الرجاء اختيار إصدار أولاً.")
            return
        scope = self.target_scope_combo.currentData()
        try:
            if scope == "all":
                self._update_manager_service.set_targets_all(self._selected_version_id)
            else:
                device_ids = self._checked_device_public_ids()
                if not device_ids:
                    QMessageBox.information(self, "الاستهداف", "الرجاء اختيار جهاز واحد على الأقل.")
                    return
                self._update_manager_service.set_targets_devices(
                    self._selected_version_id, device_public_ids=device_ids
                )
        except UpdateManagerServiceError as exc:
            QMessageBox.warning(self, "تعذّر تعيين الاستهداف", str(exc))
            return
        self._reload_detail()

    def _on_schedule_clicked(self) -> None:
        if self._selected_version_id is None:
            QMessageBox.information(self, "جدولة", "الرجاء اختيار إصدار أولاً.")
            return
        scheduled_at = self.schedule_datetime_edit.dateTime().toPython()
        try:
            self._update_manager_service.schedule(self._selected_version_id, scheduled_at=scheduled_at)
        except UpdateManagerServiceError as exc:
            QMessageBox.warning(self, "تعذّرت الجدولة", str(exc))
            return
        self._reload_versions()
        self._reload_detail()

    def _on_publish_clicked(self) -> None:
        if self._selected_version_id is None:
            QMessageBox.information(self, "نشر", "الرجاء اختيار إصدار أولاً.")
            return
        confirmed = QMessageBox.question(
            self,
            "تأكيد النشر",
            "هل تريد نشر هذا الإصدار الآن؟",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        try:
            self._update_manager_service.publish(self._selected_version_id)
        except UpdateManagerServiceError as exc:
            QMessageBox.warning(self, "تعذّر النشر", str(exc))
            return
        self._reload_versions()
        self._reload_detail()

    def _on_disable_clicked(self) -> None:
        if self._selected_version_id is None:
            QMessageBox.information(self, "تعطيل", "الرجاء اختيار إصدار أولاً.")
            return
        confirmed = QMessageBox.question(
            self,
            "تأكيد التعطيل",
            "هل تريد تعطيل هذا الإصدار؟ لن يُعرض بعد الآن على أي عميل.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        try:
            self._update_manager_service.disable(self._selected_version_id)
        except UpdateManagerServiceError as exc:
            QMessageBox.warning(self, "تعذّر التعطيل", str(exc))
            return
        self._reload_versions()
        self._reload_detail()

    def _on_rollback_clicked(self) -> None:
        if self._selected_version_id is None:
            QMessageBox.information(self, "التراجع", "الرجاء اختيار إصدار أولاً.")
            return
        confirmed = QMessageBox.question(
            self,
            "تأكيد التراجع",
            "هل تريد التراجع عن هذا الإصدار؟ لن يُحذف، لكنه لن يُعرض بعد الآن على أي عميل.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        try:
            self._update_manager_service.rollback(
                self._selected_version_id, reason=self.rollback_reason_edit.text().strip() or None
            )
        except UpdateManagerServiceError as exc:
            QMessageBox.warning(self, "تعذّر التراجع", str(exc))
            return
        self.rollback_reason_edit.clear()
        self._reload_versions()
        self._reload_detail()

"""Settings screen: company profile, preferences, and backup/restore."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from config import get_config
from controllers.settings_controller import SettingsController
from database.database import get_database
from services.subscription_check_service import SubscriptionCheckService
from sync.coordinator import ClientSyncCoordinator
from ui.subscription_info_window import SubscriptionInfoWindow
from ui.widgets import ConfirmDialog, make_primary_button, make_secondary_label, make_status_label


class _StatusMixin:
    """Shared inline status-banner behavior for every settings tab."""

    def _init_status(self, layout: QVBoxLayout) -> None:
        self._status_label = make_status_label("", "danger")
        self._status_label.hide()
        layout.addWidget(self._status_label)

    def _show_status(self, message: str, *, status: str) -> None:
        self._status_label.setProperty("status", status)
        style = self._status_label.style()
        style.unpolish(self._status_label)
        style.polish(self._status_label)
        self._status_label.setText(message)
        self._status_label.show()

    def _hide_status(self) -> None:
        self._status_label.clear()
        self._status_label.hide()


class CompanyInfoTab(_StatusMixin, QWidget):
    """Edits the company's profile fields."""

    def __init__(self, *, controller: SettingsController, parent: QWidget | None = None) -> None:
        """Create the company info tab.

        Args:
            controller: The shared settings controller.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._controller = controller

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        self._init_status(layout)

        form = QFormLayout()
        form.setSpacing(12)

        self.name_edit = QLineEdit(self)
        form.addRow("اسم الشركة *", self.name_edit)

        self.legal_name_edit = QLineEdit(self)
        form.addRow("الاسم القانوني", self.legal_name_edit)

        logo_row = QHBoxLayout()
        self.logo_path_edit = QLineEdit(self)
        logo_row.addWidget(self.logo_path_edit)
        browse_button = make_primary_button("استعراض...", parent=self)
        browse_button.clicked.connect(self._on_browse_logo_clicked)
        logo_row.addWidget(browse_button)
        form.addRow("شعار الشركة", logo_row)

        self.email_edit = QLineEdit(self)
        form.addRow("البريد الإلكتروني", self.email_edit)

        self.phone_edit = QLineEdit(self)
        form.addRow("الهاتف", self.phone_edit)

        self.address_edit = QLineEdit(self)
        form.addRow("العنوان", self.address_edit)

        self.tax_number_edit = QLineEdit(self)
        form.addRow("الرقم الضريبي", self.tax_number_edit)

        self.is_active_checkbox = QCheckBox("الشركة فعالة", self)
        form.addRow("", self.is_active_checkbox)

        layout.addLayout(form)

        save_button = make_primary_button("حفظ", parent=self)
        save_button.clicked.connect(self._on_save_clicked)
        layout.addWidget(save_button)
        layout.addStretch(1)

        self.refresh()

    def refresh(self) -> None:
        """Reload the company's profile from the database."""
        self._hide_status()
        info = self._controller.get_company_info()
        if info is None:
            return
        self.name_edit.setText(info.get("name") or "")
        self.legal_name_edit.setText(info.get("legal_name") or "")
        self.logo_path_edit.setText(info.get("logo_path") or "")
        self.email_edit.setText(info.get("email") or "")
        self.phone_edit.setText(info.get("phone") or "")
        self.address_edit.setText(info.get("address") or "")
        self.tax_number_edit.setText(info.get("tax_number") or "")
        self.is_active_checkbox.setChecked(bool(info.get("is_active", True)))

    def _on_browse_logo_clicked(self) -> None:
        """Prompt for an image file and fill in its path."""
        chosen, _filter = QFileDialog.getOpenFileName(
            self, "اختيار شعار الشركة", "", "Images (*.png *.jpg *.jpeg *.svg)"
        )
        if chosen:
            self.logo_path_edit.setText(chosen)

    def _on_save_clicked(self) -> None:
        """Persist the form's current state."""
        self._hide_status()
        name = self.name_edit.text().strip()
        if not name:
            self._show_status("اسم الشركة حقل إلزامي.", status="danger")
            return
        result = self._controller.update_company_info(
            name=name,
            legal_name=self.legal_name_edit.text().strip() or None,
            logo_path=self.logo_path_edit.text().strip() or None,
            email=self.email_edit.text().strip() or None,
            phone=self.phone_edit.text().strip() or None,
            address=self.address_edit.text().strip() or None,
            tax_number=self.tax_number_edit.text().strip() or None,
            is_active=self.is_active_checkbox.isChecked(),
        )
        if result is not None:
            self._show_status("تم حفظ معلومات الشركة بنجاح.", status="success")


class PreferencesTab(_StatusMixin, QWidget):
    """Edits company-wide display and operational preferences."""

    def __init__(self, *, controller: SettingsController, parent: QWidget | None = None) -> None:
        """Create the preferences tab.

        Args:
            controller: The shared settings controller.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._controller = controller

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        self._init_status(layout)

        form = QFormLayout()
        form.setSpacing(12)

        self.timezone_edit = QLineEdit(self)
        self.timezone_edit.setPlaceholderText("Asia/Baghdad")
        form.addRow("المنطقة الزمنية", self.timezone_edit)

        self.date_format_edit = QLineEdit(self)
        self.date_format_edit.setPlaceholderText("%d/%m/%Y")
        form.addRow("صيغة التاريخ", self.date_format_edit)

        self.time_24h_checkbox = QCheckBox("نظام 24 ساعة", self)
        form.addRow("", self.time_24h_checkbox)

        self.currency_code_edit = QLineEdit(self)
        form.addRow("رمز العملة", self.currency_code_edit)

        self.currency_symbol_edit = QLineEdit(self)
        form.addRow("رمز العملة المعروض", self.currency_symbol_edit)

        self.auto_backup_checkbox = QCheckBox("تفعيل النسخ الاحتياطي التلقائي", self)
        form.addRow("", self.auto_backup_checkbox)

        self.backup_interval_spin = QSpinBox(self)
        self.backup_interval_spin.setRange(1, 168)
        self.backup_interval_spin.setSuffix(" ساعة")
        form.addRow("فاصل النسخ الاحتياطي", self.backup_interval_spin)

        self.backup_retention_spin = QSpinBox(self)
        self.backup_retention_spin.setRange(1, 365)
        form.addRow("عدد النسخ المحتفظ بها", self.backup_retention_spin)

        self.device_timeout_spin = QSpinBox(self)
        self.device_timeout_spin.setRange(1, 120)
        self.device_timeout_spin.setSuffix(" ثانية")
        form.addRow("مهلة اتصال الجهاز الافتراضية", self.device_timeout_spin)

        self.sync_interval_spin = QSpinBox(self)
        self.sync_interval_spin.setRange(1, 1440)
        self.sync_interval_spin.setSuffix(" دقيقة")
        form.addRow("فاصل مزامنة الأجهزة الافتراضي", self.sync_interval_spin)

        layout.addLayout(form)

        save_button = make_primary_button("حفظ", parent=self)
        save_button.clicked.connect(self._on_save_clicked)
        layout.addWidget(save_button)
        layout.addStretch(1)

        self.refresh()

    def refresh(self) -> None:
        """Reload preferences from the database (seeding defaults if needed)."""
        self._hide_status()
        settings = self._controller.get_settings()
        if settings is None:
            return
        self.timezone_edit.setText(settings.get("timezone_name") or "")
        self.date_format_edit.setText(settings.get("date_format") or "")
        self.time_24h_checkbox.setChecked(bool(settings.get("time_format_24h", True)))
        self.currency_code_edit.setText(settings.get("currency_code") or "")
        self.currency_symbol_edit.setText(settings.get("currency_symbol") or "")
        self.auto_backup_checkbox.setChecked(bool(settings.get("auto_backup_enabled", True)))
        self.backup_interval_spin.setValue(int(settings.get("backup_interval_hours") or 24))
        self.backup_retention_spin.setValue(int(settings.get("backup_retention_count") or 14))
        self.device_timeout_spin.setValue(
            int(settings.get("default_device_timeout_seconds") or 8)
        )
        self.sync_interval_spin.setValue(
            int(settings.get("default_sync_interval_minutes") or 15)
        )

    def _on_save_clicked(self) -> None:
        """Persist the form's current state."""
        self._hide_status()
        timezone_name = self.timezone_edit.text().strip()
        if not timezone_name:
            self._show_status("المنطقة الزمنية حقل إلزامي.", status="danger")
            return
        result = self._controller.update_settings(
            timezone_name=timezone_name,
            date_format=self.date_format_edit.text().strip() or "%d/%m/%Y",
            time_format_24h=self.time_24h_checkbox.isChecked(),
            currency_code=self.currency_code_edit.text().strip() or "IQD",
            currency_symbol=self.currency_symbol_edit.text().strip() or "د.ع",
            auto_backup_enabled=self.auto_backup_checkbox.isChecked(),
            backup_interval_hours=self.backup_interval_spin.value(),
            backup_retention_count=self.backup_retention_spin.value(),
            default_device_timeout_seconds=self.device_timeout_spin.value(),
            default_sync_interval_minutes=self.sync_interval_spin.value(),
        )
        if result is not None:
            self._show_status("تم حفظ التفضيلات بنجاح.", status="success")


class BackupTab(_StatusMixin, QWidget):
    """Creates, lists, and restores database backups."""

    def __init__(self, *, controller: SettingsController, parent: QWidget | None = None) -> None:
        """Create the backup tab.

        Args:
            controller: The shared settings controller.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._controller = controller

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        self._init_status(layout)

        actions_row = QHBoxLayout()
        create_button = make_primary_button("+ إنشاء نسخة احتياطية الآن", parent=self)
        create_button.clicked.connect(self._on_create_clicked)
        actions_row.addWidget(create_button)
        actions_row.addStretch(1)
        layout.addLayout(actions_row)

        layout.addWidget(make_secondary_label("النسخ الاحتياطية المتوفرة:"))
        self.backups_list = QListWidget(self)
        layout.addWidget(self.backups_list, stretch=1)

        self.restore_button = make_primary_button("استعادة من النسخة المحددة", parent=self)
        self.restore_button.setEnabled(False)
        self.restore_button.clicked.connect(self._on_restore_clicked)
        layout.addWidget(self.restore_button)

        self.backups_list.itemSelectionChanged.connect(self._on_selection_changed)

        self.refresh()

    def refresh(self) -> None:
        """Reload the backup file list."""
        self._hide_status()
        self.backups_list.clear()
        for path in self._controller.list_backups():
            self.backups_list.addItem(QListWidgetItem(path))
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        """Enable the restore button only when a backup is selected."""
        self.restore_button.setEnabled(bool(self.backups_list.selectedItems()))

    def _on_create_clicked(self) -> None:
        """Create a new backup and refresh the list.

        Refreshes the list *before* showing the success message -
        refresh() itself clears any current status banner, so showing
        the message first would have it wiped out immediately.
        """
        self._hide_status()
        path = self._controller.create_backup()
        if path is not None:
            self.refresh()
            self._show_status(f"تم إنشاء نسخة احتياطية: {path}", status="success")

    def _on_restore_clicked(self) -> None:
        """Confirm and restore the database from the selected backup.

        This overwrites the current, live database with the backup's
        contents - an irreversible action from the running
        application's point of view, so the confirmation text spells
        that out explicitly rather than using a generic "are you sure?".
        """
        items = self.backups_list.selectedItems()
        if not items:
            return
        backup_path = items[0].text()
        confirmed = ConfirmDialog.confirm(
            self,
            "تأكيد استعادة النسخة الاحتياطية",
            "سيتم استبدال جميع البيانات الحالية ببيانات هذه النسخة الاحتياطية "
            "بشكل نهائي. يُنصح بإنشاء نسخة احتياطية جديدة قبل المتابعة. "
            "هل تريد المتابعة؟",
            danger=True,
        )
        if not confirmed:
            return
        self._hide_status()
        success = self._controller.restore_backup(Path(backup_path))
        if success:
            self._show_status(
                "تمت استعادة النسخة الاحتياطية بنجاح. يُنصح بإعادة تشغيل التطبيق.",
                status="success",
            )


class SettingsPage(QTabWidget):
    """The company settings screen: profile, preferences, and backup."""

    def __init__(
        self,
        *,
        company_id: int,
        current_user_id: int | None = None,
        permission_codes: frozenset[str] = frozenset(),
        parent: QWidget | None = None,
    ) -> None:
        """Create the settings page.

        Args:
            company_id: The company these settings belong to.
            current_user_id: The signed-in user, for audit attribution.
            permission_codes: The signed-in user's granted permission
                codes.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        controller = SettingsController(
            company_id=company_id,
            actor_user_id=current_user_id,
            permission_codes=permission_codes,
        )
        controller.operation_failed.connect(self._on_operation_failed)
        self._controller = controller

        self.company_info_tab = CompanyInfoTab(controller=controller, parent=self)
        self.preferences_tab = PreferencesTab(controller=controller, parent=self)
        self.backup_tab = BackupTab(controller=controller, parent=self)
        config = get_config()
        sync_coordinator = ClientSyncCoordinator(get_database(), config.sync.server_url)
        subscription_check_service = SubscriptionCheckService(
            get_database(), sync_coordinator, device_name=config.sync.device_name
        )
        self.subscription_tab = SubscriptionInfoWindow(
            check_service=subscription_check_service,
            can_change_company_code="settings.manage" in permission_codes,
            parent=self,
        )

        self.addTab(self.company_info_tab, "معلومات الشركة")
        self.addTab(self.preferences_tab, "التفضيلات")
        self.addTab(self.backup_tab, "النسخ الاحتياطي")
        self.addTab(self.subscription_tab, "الاشتراك")

    def _on_operation_failed(self, message: str) -> None:
        """Surface a controller failure on whichever tab is currently visible."""
        current = self.currentWidget()
        if isinstance(current, _StatusMixin):
            current._show_status(message, status="danger")

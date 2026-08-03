"""Add/edit form dialogs for the five Remote Configuration profile types.

Kept in one file since each is a small, single-purpose
:class:`~PySide6.QtWidgets.QDialog` following the same shape as
:class:`~developer_suite.ui.customer_form_dialog.CustomerFormDialog`:
pre-fill from an optional existing entity, read values back via
``field_values()`` after ``exec()`` returns accepted. None of them talk
to :mod:`developer_suite.services.configuration_service` directly.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from developer_suite.models.attendance_policy_profile import AttendancePolicyProfile
from developer_suite.models.backup_profile import BackupLocationType, BackupProfile
from developer_suite.models.device_profile import DeviceProfile
from developer_suite.models.print_profile import PaperSize, PrintProfile
from developer_suite.models.theme_profile import ThemeMode, ThemeProfile
from models.enums import DeviceProtocol, Weekday

_PAPER_SIZE_LABELS_AR = {
    PaperSize.A4: "A4",
    PaperSize.A5: "A5",
    PaperSize.THERMAL_80MM: "طابعة حرارية 80مم",
}
_BACKUP_LOCATION_LABELS_AR = {
    BackupLocationType.LOCAL: "محلي",
    BackupLocationType.CLOUD: "سحابي",
}
_THEME_MODE_LABELS_AR = {
    ThemeMode.LIGHT: "فاتح",
    ThemeMode.DARK: "داكن",
}


class ThemeProfileDialog(QDialog):
    """Add/edit form for a single :class:`~developer_suite.models.theme_profile.ThemeProfile`."""

    def __init__(self, *, existing: ThemeProfile | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("تعديل نمط المظهر" if existing else "نمط مظهر جديد")
        self.setMinimumWidth(420)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.name_edit = QLineEdit(self)
        form.addRow("الاسم *", self.name_edit)

        self.mode_combo = QComboBox(self)
        for mode in ThemeMode:
            self.mode_combo.addItem(_THEME_MODE_LABELS_AR[mode], userData=mode)
        form.addRow("الوضع", self.mode_combo)

        self.primary_color_edit = QLineEdit(self)
        self.primary_color_edit.setPlaceholderText("#1976D2")
        form.addRow("اللون الأساسي", self.primary_color_edit)

        self.secondary_color_edit = QLineEdit(self)
        self.secondary_color_edit.setPlaceholderText("#424242")
        form.addRow("اللون الثانوي", self.secondary_color_edit)

        self.accent_color_edit = QLineEdit(self)
        form.addRow("لون التمييز", self.accent_color_edit)

        self.logo_path_edit = QLineEdit(self)
        form.addRow("مسار الشعار", self.logo_path_edit)

        self.font_family_edit = QLineEdit(self)
        form.addRow("الخط", self.font_family_edit)

        if existing is not None:
            self.name_edit.setText(existing.name)
            self.mode_combo.setCurrentIndex(list(ThemeMode).index(existing.mode))
            self.primary_color_edit.setText(existing.primary_color)
            self.secondary_color_edit.setText(existing.secondary_color)
            self.accent_color_edit.setText(existing.accent_color or "")
            self.logo_path_edit.setText(existing.logo_path or "")
            self.font_family_edit.setText(existing.font_family)
        else:
            self.primary_color_edit.setText("#1976D2")
            self.secondary_color_edit.setText("#424242")
            self.font_family_edit.setText("Cairo")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def field_values(self) -> dict[str, object]:
        """Values ready to pass to ``create_theme_profile``/``update_theme_profile``."""
        return {
            "name": self.name_edit.text().strip(),
            "mode": ThemeMode(self.mode_combo.currentData()),
            "primary_color": self.primary_color_edit.text().strip(),
            "secondary_color": self.secondary_color_edit.text().strip(),
            "accent_color": self.accent_color_edit.text().strip() or None,
            "logo_path": self.logo_path_edit.text().strip() or None,
            "font_family": self.font_family_edit.text().strip() or "Cairo",
        }


class PrintProfileDialog(QDialog):
    """Add/edit form for a single :class:`~developer_suite.models.print_profile.PrintProfile`."""

    def __init__(self, *, existing: PrintProfile | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("تعديل نمط الطباعة" if existing else "نمط طباعة جديد")
        self.setMinimumWidth(420)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.name_edit = QLineEdit(self)
        form.addRow("الاسم *", self.name_edit)

        self.paper_size_combo = QComboBox(self)
        for size in PaperSize:
            self.paper_size_combo.addItem(_PAPER_SIZE_LABELS_AR[size], userData=size)
        form.addRow("حجم الورق", self.paper_size_combo)

        self.header_text_edit = QLineEdit(self)
        form.addRow("نص الترويسة", self.header_text_edit)

        self.footer_text_edit = QLineEdit(self)
        form.addRow("نص التذييل", self.footer_text_edit)

        self.show_company_logo_check = QCheckBox("إظهار شعار الشركة", self)
        self.show_company_logo_check.setChecked(True)
        form.addRow("", self.show_company_logo_check)

        self.show_qr_code_check = QCheckBox("إظهار رمز QR", self)
        self.show_qr_code_check.setChecked(True)
        form.addRow("", self.show_qr_code_check)

        self.margin_mm_spin = QSpinBox(self)
        self.margin_mm_spin.setRange(0, 100)
        self.margin_mm_spin.setValue(15)
        form.addRow("الهامش (مم)", self.margin_mm_spin)

        if existing is not None:
            self.name_edit.setText(existing.name)
            self.paper_size_combo.setCurrentIndex(list(PaperSize).index(existing.paper_size))
            self.header_text_edit.setText(existing.header_text or "")
            self.footer_text_edit.setText(existing.footer_text or "")
            self.show_company_logo_check.setChecked(existing.show_company_logo)
            self.show_qr_code_check.setChecked(existing.show_qr_code)
            self.margin_mm_spin.setValue(existing.margin_mm)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def field_values(self) -> dict[str, object]:
        """Values ready to pass to ``create_print_profile``/``update_print_profile``."""
        return {
            "name": self.name_edit.text().strip(),
            "paper_size": PaperSize(self.paper_size_combo.currentData()),
            "header_text": self.header_text_edit.text().strip() or None,
            "footer_text": self.footer_text_edit.text().strip() or None,
            "show_company_logo": self.show_company_logo_check.isChecked(),
            "show_qr_code": self.show_qr_code_check.isChecked(),
            "margin_mm": self.margin_mm_spin.value(),
        }


class AttendancePolicyProfileDialog(QDialog):
    """Add/edit form for a single
    :class:`~developer_suite.models.attendance_policy_profile.AttendancePolicyProfile`.
    """

    def __init__(
        self, *, existing: AttendancePolicyProfile | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("تعديل سياسة الحضور" if existing else "سياسة حضور جديدة")
        self.setMinimumWidth(440)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.name_edit = QLineEdit(self)
        form.addRow("الاسم *", self.name_edit)

        self.grace_period_spin = QSpinBox(self)
        self.grace_period_spin.setRange(0, 240)
        form.addRow("فترة سماح الحضور (دقيقة)", self.grace_period_spin)

        self.early_leave_grace_spin = QSpinBox(self)
        self.early_leave_grace_spin.setRange(0, 240)
        form.addRow("فترة سماح الانصراف المبكر (دقيقة)", self.early_leave_grace_spin)

        self.overtime_threshold_spin = QSpinBox(self)
        self.overtime_threshold_spin.setRange(0, 240)
        form.addRow("حد بدء العمل الإضافي (دقيقة)", self.overtime_threshold_spin)

        self.half_day_threshold_spin = QSpinBox(self)
        self.half_day_threshold_spin.setRange(1, 12)
        self.half_day_threshold_spin.setValue(4)
        form.addRow("حد نصف اليوم (ساعة)", self.half_day_threshold_spin)

        self._working_day_checks: dict[Weekday, QCheckBox] = {}
        working_days_row = QHBoxLayout()
        for weekday in Weekday:
            checkbox = QCheckBox(weekday.label_ar, self)
            self._working_day_checks[weekday] = checkbox
            working_days_row.addWidget(checkbox)
        form.addRow("أيام العمل", working_days_row)

        if existing is not None:
            self.name_edit.setText(existing.name)
            self.grace_period_spin.setValue(existing.grace_period_minutes)
            self.early_leave_grace_spin.setValue(existing.early_leave_grace_minutes)
            self.overtime_threshold_spin.setValue(existing.overtime_threshold_minutes)
            self.half_day_threshold_spin.setValue(existing.half_day_threshold_hours)
            for weekday, checkbox in self._working_day_checks.items():
                checkbox.setChecked(weekday.value in existing.working_days)
        else:
            for weekday, checkbox in self._working_day_checks.items():
                checkbox.setChecked(weekday is not Weekday.FRIDAY)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def field_values(self) -> dict[str, object]:
        """Values ready to pass to ``create_attendance_policy_profile``/``update_attendance_policy_profile``."""
        return {
            "name": self.name_edit.text().strip(),
            "grace_period_minutes": self.grace_period_spin.value(),
            "early_leave_grace_minutes": self.early_leave_grace_spin.value(),
            "overtime_threshold_minutes": self.overtime_threshold_spin.value(),
            "half_day_threshold_hours": self.half_day_threshold_spin.value(),
            "working_days": [
                weekday.value
                for weekday, checkbox in self._working_day_checks.items()
                if checkbox.isChecked()
            ],
        }


class DeviceProfileDialog(QDialog):
    """Add/edit form for a single :class:`~developer_suite.models.device_profile.DeviceProfile`."""

    def __init__(self, *, existing: DeviceProfile | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("تعديل نمط الجهاز" if existing else "نمط جهاز جديد")
        self.setMinimumWidth(420)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.name_edit = QLineEdit(self)
        form.addRow("الاسم *", self.name_edit)

        self.protocol_combo = QComboBox(self)
        for protocol in DeviceProtocol:
            self.protocol_combo.addItem(protocol.label_ar, userData=protocol)
        form.addRow("البروتوكول", self.protocol_combo)

        self.default_port_spin = QSpinBox(self)
        self.default_port_spin.setRange(1, 65535)
        self.default_port_spin.setValue(4370)
        form.addRow("المنفذ الافتراضي", self.default_port_spin)

        self.timeout_seconds_spin = QSpinBox(self)
        self.timeout_seconds_spin.setRange(1, 120)
        self.timeout_seconds_spin.setValue(8)
        form.addRow("مهلة الاتصال (ثانية)", self.timeout_seconds_spin)

        self.sync_interval_spin = QSpinBox(self)
        self.sync_interval_spin.setRange(1, 1440)
        self.sync_interval_spin.setValue(15)
        form.addRow("فترة المزامنة (دقيقة)", self.sync_interval_spin)

        self.auto_reconnect_check = QCheckBox("إعادة الاتصال تلقائياً", self)
        self.auto_reconnect_check.setChecked(True)
        form.addRow("", self.auto_reconnect_check)

        if existing is not None:
            self.name_edit.setText(existing.name)
            self.protocol_combo.setCurrentIndex(list(DeviceProtocol).index(existing.protocol))
            self.default_port_spin.setValue(existing.default_port)
            self.timeout_seconds_spin.setValue(existing.timeout_seconds)
            self.sync_interval_spin.setValue(existing.sync_interval_minutes)
            self.auto_reconnect_check.setChecked(existing.auto_reconnect)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def field_values(self) -> dict[str, object]:
        """Values ready to pass to ``create_device_profile``/``update_device_profile``."""
        return {
            "name": self.name_edit.text().strip(),
            "protocol": DeviceProtocol(self.protocol_combo.currentData()),
            "default_port": self.default_port_spin.value(),
            "timeout_seconds": self.timeout_seconds_spin.value(),
            "sync_interval_minutes": self.sync_interval_spin.value(),
            "auto_reconnect": self.auto_reconnect_check.isChecked(),
        }


class BackupProfileDialog(QDialog):
    """Add/edit form for a single :class:`~developer_suite.models.backup_profile.BackupProfile`."""

    def __init__(self, *, existing: BackupProfile | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("تعديل نمط النسخ الاحتياطي" if existing else "نمط نسخ احتياطي جديد")
        self.setMinimumWidth(420)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.name_edit = QLineEdit(self)
        form.addRow("الاسم *", self.name_edit)

        self.enabled_check = QCheckBox("تفعيل النسخ التلقائي", self)
        self.enabled_check.setChecked(True)
        form.addRow("", self.enabled_check)

        self.interval_hours_spin = QSpinBox(self)
        self.interval_hours_spin.setRange(1, 168)
        self.interval_hours_spin.setValue(24)
        form.addRow("الفترة (ساعة)", self.interval_hours_spin)

        self.retention_count_spin = QSpinBox(self)
        self.retention_count_spin.setRange(1, 365)
        self.retention_count_spin.setValue(14)
        form.addRow("عدد النسخ المحتفظ بها", self.retention_count_spin)

        self.location_type_combo = QComboBox(self)
        for location in BackupLocationType:
            self.location_type_combo.addItem(_BACKUP_LOCATION_LABELS_AR[location], userData=location)
        form.addRow("موقع التخزين", self.location_type_combo)

        self.encrypt_backups_check = QCheckBox("تشفير النسخ الاحتياطية", self)
        self.encrypt_backups_check.setChecked(True)
        form.addRow("", self.encrypt_backups_check)

        if existing is not None:
            self.name_edit.setText(existing.name)
            self.enabled_check.setChecked(existing.enabled)
            self.interval_hours_spin.setValue(existing.interval_hours)
            self.retention_count_spin.setValue(existing.retention_count)
            self.location_type_combo.setCurrentIndex(
                list(BackupLocationType).index(existing.location_type)
            )
            self.encrypt_backups_check.setChecked(existing.encrypt_backups)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def field_values(self) -> dict[str, object]:
        """Values ready to pass to ``create_backup_profile``/``update_backup_profile``."""
        return {
            "name": self.name_edit.text().strip(),
            "enabled": self.enabled_check.isChecked(),
            "interval_hours": self.interval_hours_spin.value(),
            "retention_count": self.retention_count_spin.value(),
            "location_type": BackupLocationType(self.location_type_combo.currentData()),
            "encrypt_backups": self.encrypt_backups_check.isChecked(),
        }

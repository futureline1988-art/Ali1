"""Add/edit form dialog for a :class:`~developer_suite.models.remote_configuration.RemoteConfiguration` bundle.

Composes five existing profiles by reference (picked from combo boxes)
rather than editing any profile's own fields — those are edited on
their own tab in
:mod:`developer_suite.ui.configuration_editor_page` via
:mod:`developer_suite.ui.profile_dialogs`.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QPlainTextEdit,
    QWidget,
)

from developer_suite.models.attendance_policy_profile import AttendancePolicyProfile
from developer_suite.models.backup_profile import BackupProfile
from developer_suite.models.device_profile import DeviceProfile
from developer_suite.models.print_profile import PrintProfile
from developer_suite.models.remote_configuration import RemoteConfiguration
from developer_suite.models.theme_profile import ThemeProfile


class RemoteConfigurationDialog(QDialog):
    """Add/edit form for a configuration bundle: a name plus five profile picks."""

    def __init__(
        self,
        *,
        theme_profiles: list[ThemeProfile],
        print_profiles: list[PrintProfile],
        attendance_policy_profiles: list[AttendancePolicyProfile],
        device_profiles: list[DeviceProfile],
        backup_profiles: list[BackupProfile],
        existing: RemoteConfiguration | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Build the form.

        Args:
            theme_profiles: Every selectable theme profile.
            print_profiles: Every selectable print profile.
            attendance_policy_profiles: Every selectable attendance
                policy profile.
            device_profiles: Every selectable device profile.
            backup_profiles: Every selectable backup profile.
            existing: The bundle being edited, or ``None`` to create a
                new one.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("تعديل حزمة الإعدادات" if existing else "حزمة إعدادات جديدة")
        self.setMinimumWidth(440)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.name_edit = QLineEdit(self)
        form.addRow("الاسم *", self.name_edit)

        self.description_edit = QPlainTextEdit(self)
        self.description_edit.setFixedHeight(70)
        form.addRow("الوصف", self.description_edit)

        self.theme_combo = self._build_profile_combo(theme_profiles)
        form.addRow("نمط المظهر *", self.theme_combo)

        self.print_combo = self._build_profile_combo(print_profiles)
        form.addRow("نمط الطباعة *", self.print_combo)

        self.attendance_policy_combo = self._build_profile_combo(attendance_policy_profiles)
        form.addRow("سياسة الحضور *", self.attendance_policy_combo)

        self.device_combo = self._build_profile_combo(device_profiles)
        form.addRow("نمط الجهاز *", self.device_combo)

        self.backup_combo = self._build_profile_combo(backup_profiles)
        form.addRow("نمط النسخ الاحتياطي *", self.backup_combo)

        if existing is not None:
            self.name_edit.setText(existing.name)
            self.description_edit.setPlainText(existing.description or "")
            self._select_id(self.theme_combo, existing.theme_profile_id)
            self._select_id(self.print_combo, existing.print_profile_id)
            self._select_id(self.attendance_policy_combo, existing.attendance_policy_profile_id)
            self._select_id(self.device_combo, existing.device_profile_id)
            self._select_id(self.backup_combo, existing.backup_profile_id)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    @staticmethod
    def _build_profile_combo(profiles: list) -> QComboBox:
        """A combo box listing ``profiles`` by name, ``id`` as each item's data."""
        combo = QComboBox()
        for profile in profiles:
            combo.addItem(profile.name, userData=profile.id)
        return combo

    @staticmethod
    def _select_id(combo: QComboBox, profile_id: int) -> None:
        """Select the item whose stored id matches ``profile_id``, if present."""
        index = combo.findData(profile_id)
        if index >= 0:
            combo.setCurrentIndex(index)

    def field_values(self) -> dict[str, object]:
        """Values ready to pass to ``create_configuration``/``update_configuration``.

        Returns:
            A dict with keys ``name``, ``description``,
            ``theme_profile_id``, ``print_profile_id``,
            ``attendance_policy_profile_id``, ``device_profile_id``,
            ``backup_profile_id``.
        """
        return {
            "name": self.name_edit.text().strip(),
            "description": self.description_edit.toPlainText().strip() or None,
            "theme_profile_id": self.theme_combo.currentData(),
            "print_profile_id": self.print_combo.currentData(),
            "attendance_policy_profile_id": self.attendance_policy_combo.currentData(),
            "device_profile_id": self.device_combo.currentData(),
            "backup_profile_id": self.backup_combo.currentData(),
        }

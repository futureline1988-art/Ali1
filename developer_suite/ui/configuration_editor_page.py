"""Remote Configuration editor page: one tab per profile type, plus bundles and publishing.

Every profile/bundle tab is a
:class:`~developer_suite.ui._profile_list_panel.ProfileListPanel` wired
to one group of :class:`~developer_suite.services.configuration_service.ConfigurationService`
methods — unchanged since Phase 5. Phase 13 adds one more tab,
:class:`~developer_suite.ui.configuration_publish_panel.ConfigurationPublishPanel`,
for publishing a bundle to a customer's installation, viewing its
version history, comparing pending changes, and rolling back — see
that module's own docstring for its dependencies.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QTabWidget, QVBoxLayout, QWidget

from developer_suite.admin.client import AdminApiClient
from developer_suite.admin.session_manager import AdminSessionManager
from developer_suite.models.attendance_policy_profile import AttendancePolicyProfile
from developer_suite.models.backup_profile import BackupProfile
from developer_suite.models.device_profile import DeviceProfile
from developer_suite.models.print_profile import PrintProfile
from developer_suite.models.remote_configuration import RemoteConfiguration
from developer_suite.models.theme_profile import ThemeProfile
from developer_suite.services.configuration_publish_service import ConfigurationPublishService
from developer_suite.services.configuration_service import (
    ConfigurationService,
    ConfigurationServiceError,
)
from developer_suite.services.customer_service import CustomerService
from developer_suite.ui._profile_list_panel import ProfileListPanel
from developer_suite.ui.configuration_publish_panel import ConfigurationPublishPanel
from developer_suite.ui.profile_dialogs import (
    AttendancePolicyProfileDialog,
    BackupProfileDialog,
    DeviceProfileDialog,
    PrintProfileDialog,
    ThemeProfileDialog,
)
from developer_suite.ui.remote_configuration_dialog import RemoteConfigurationDialog


class ConfigurationEditorPage(QWidget):
    """The Remote Configuration module's main content page.

    Talks only to
    :class:`~developer_suite.services.configuration_service.ConfigurationService`
    — never to :class:`~developer_suite.repositories.configuration_repository.ConfigurationRepository`
    directly, matching every other module's established service/UI
    boundary. The publishing tab additionally talks to
    :class:`~developer_suite.services.configuration_publish_service.ConfigurationPublishService`,
    :class:`~developer_suite.services.customer_service.CustomerService`,
    and :class:`~developer_suite.admin.client.AdminApiClient` — see
    :class:`~developer_suite.ui.configuration_publish_panel.ConfigurationPublishPanel`'s
    own docstring.
    """

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
        """Build the page with one tab per profile type, plus bundles and publishing.

        Args:
            configuration_service: The service every profile/bundle tab
                performs its operations through.
            publish_service: Backs the publishing tab.
            customer_service: Populates the publishing tab's customer
                picker.
            admin_client: Populates the publishing tab's target
                -installation picker.
            admin_session_manager: Supplies the current administrator's
                identity to the publishing tab.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._service = configuration_service

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)

        self.theme_panel = self._build_theme_tab()
        self.tabs.addTab(self.theme_panel, "أنماط المظهر")

        self.print_panel = self._build_print_tab()
        self.tabs.addTab(self.print_panel, "أنماط الطباعة")

        self.attendance_policy_panel = self._build_attendance_policy_tab()
        self.tabs.addTab(self.attendance_policy_panel, "سياسات الحضور")

        self.device_panel = self._build_device_tab()
        self.tabs.addTab(self.device_panel, "أنماط الأجهزة")

        self.backup_panel = self._build_backup_tab()
        self.tabs.addTab(self.backup_panel, "أنماط النسخ الاحتياطي")

        self.configuration_panel = self._build_configuration_tab()
        self.tabs.addTab(self.configuration_panel, "حزم الإعدادات")

        self.publish_panel = ConfigurationPublishPanel(
            configuration_service,
            publish_service,
            customer_service,
            admin_client,
            admin_session_manager,
            parent=self,
        )
        self.tabs.addTab(self.publish_panel, "النشر")

    # -- Theme tab ----------------------------------------------------------

    def _build_theme_tab(self) -> ProfileListPanel[ThemeProfile]:
        def on_add() -> bool:
            dialog = ThemeProfileDialog(existing=None, parent=self)
            if dialog.exec() != ThemeProfileDialog.DialogCode.Accepted:
                return False
            return self._run(lambda: self._service.create_theme_profile(**dialog.field_values()))

        def on_edit(profile: ThemeProfile) -> bool:
            dialog = ThemeProfileDialog(existing=profile, parent=self)
            if dialog.exec() != ThemeProfileDialog.DialogCode.Accepted:
                return False
            return self._run(
                lambda: self._service.update_theme_profile(profile.id, **dialog.field_values())
            )

        def on_delete(profile: ThemeProfile) -> bool:
            return self._run(lambda: self._service.delete_theme_profile(profile.id))

        return ProfileListPanel[ThemeProfile](
            column_labels=("الاسم", "الوضع", "اللون الأساسي", "الخط"),
            row_values=lambda p: (p.name, p.mode.value, p.primary_color, p.font_family),
            list_items=self._service.list_theme_profiles,
            on_add=on_add,
            on_edit=on_edit,
            on_delete=on_delete,
            add_label="نمط مظهر جديد",
            edit_label="تعديل",
            delete_label="حذف",
            delete_confirm_text=lambda p: f"هل تريد حذف نمط المظهر «{p.name}»؟",
            parent=self,
        )

    # -- Print tab ------------------------------------------------------------

    def _build_print_tab(self) -> ProfileListPanel[PrintProfile]:
        def on_add() -> bool:
            dialog = PrintProfileDialog(existing=None, parent=self)
            if dialog.exec() != PrintProfileDialog.DialogCode.Accepted:
                return False
            return self._run(lambda: self._service.create_print_profile(**dialog.field_values()))

        def on_edit(profile: PrintProfile) -> bool:
            dialog = PrintProfileDialog(existing=profile, parent=self)
            if dialog.exec() != PrintProfileDialog.DialogCode.Accepted:
                return False
            return self._run(
                lambda: self._service.update_print_profile(profile.id, **dialog.field_values())
            )

        def on_delete(profile: PrintProfile) -> bool:
            return self._run(lambda: self._service.delete_print_profile(profile.id))

        return ProfileListPanel[PrintProfile](
            column_labels=("الاسم", "حجم الورق", "الهامش (مم)"),
            row_values=lambda p: (p.name, p.paper_size.value, str(p.margin_mm)),
            list_items=self._service.list_print_profiles,
            on_add=on_add,
            on_edit=on_edit,
            on_delete=on_delete,
            add_label="نمط طباعة جديد",
            edit_label="تعديل",
            delete_label="حذف",
            delete_confirm_text=lambda p: f"هل تريد حذف نمط الطباعة «{p.name}»؟",
            parent=self,
        )

    # -- Attendance policy tab -------------------------------------------------

    def _build_attendance_policy_tab(self) -> ProfileListPanel[AttendancePolicyProfile]:
        def on_add() -> bool:
            dialog = AttendancePolicyProfileDialog(existing=None, parent=self)
            if dialog.exec() != AttendancePolicyProfileDialog.DialogCode.Accepted:
                return False
            return self._run(
                lambda: self._service.create_attendance_policy_profile(**dialog.field_values())
            )

        def on_edit(profile: AttendancePolicyProfile) -> bool:
            dialog = AttendancePolicyProfileDialog(existing=profile, parent=self)
            if dialog.exec() != AttendancePolicyProfileDialog.DialogCode.Accepted:
                return False
            return self._run(
                lambda: self._service.update_attendance_policy_profile(
                    profile.id, **dialog.field_values()
                )
            )

        def on_delete(profile: AttendancePolicyProfile) -> bool:
            return self._run(lambda: self._service.delete_attendance_policy_profile(profile.id))

        return ProfileListPanel[AttendancePolicyProfile](
            column_labels=("الاسم", "فترة السماح (دقيقة)", "أيام العمل"),
            row_values=lambda p: (p.name, str(p.grace_period_minutes), str(len(p.working_days))),
            list_items=self._service.list_attendance_policy_profiles,
            on_add=on_add,
            on_edit=on_edit,
            on_delete=on_delete,
            add_label="سياسة حضور جديدة",
            edit_label="تعديل",
            delete_label="حذف",
            delete_confirm_text=lambda p: f"هل تريد حذف سياسة الحضور «{p.name}»؟",
            parent=self,
        )

    # -- Device tab -----------------------------------------------------------

    def _build_device_tab(self) -> ProfileListPanel[DeviceProfile]:
        def on_add() -> bool:
            dialog = DeviceProfileDialog(existing=None, parent=self)
            if dialog.exec() != DeviceProfileDialog.DialogCode.Accepted:
                return False
            return self._run(lambda: self._service.create_device_profile(**dialog.field_values()))

        def on_edit(profile: DeviceProfile) -> bool:
            dialog = DeviceProfileDialog(existing=profile, parent=self)
            if dialog.exec() != DeviceProfileDialog.DialogCode.Accepted:
                return False
            return self._run(
                lambda: self._service.update_device_profile(profile.id, **dialog.field_values())
            )

        def on_delete(profile: DeviceProfile) -> bool:
            return self._run(lambda: self._service.delete_device_profile(profile.id))

        return ProfileListPanel[DeviceProfile](
            column_labels=("الاسم", "البروتوكول", "المنفذ", "فترة المزامنة (دقيقة)"),
            row_values=lambda p: (
                p.name,
                p.protocol.label_ar,
                str(p.default_port),
                str(p.sync_interval_minutes),
            ),
            list_items=self._service.list_device_profiles,
            on_add=on_add,
            on_edit=on_edit,
            on_delete=on_delete,
            add_label="نمط جهاز جديد",
            edit_label="تعديل",
            delete_label="حذف",
            delete_confirm_text=lambda p: f"هل تريد حذف نمط الجهاز «{p.name}»؟",
            parent=self,
        )

    # -- Backup tab -----------------------------------------------------------

    def _build_backup_tab(self) -> ProfileListPanel[BackupProfile]:
        def on_add() -> bool:
            dialog = BackupProfileDialog(existing=None, parent=self)
            if dialog.exec() != BackupProfileDialog.DialogCode.Accepted:
                return False
            return self._run(lambda: self._service.create_backup_profile(**dialog.field_values()))

        def on_edit(profile: BackupProfile) -> bool:
            dialog = BackupProfileDialog(existing=profile, parent=self)
            if dialog.exec() != BackupProfileDialog.DialogCode.Accepted:
                return False
            return self._run(
                lambda: self._service.update_backup_profile(profile.id, **dialog.field_values())
            )

        def on_delete(profile: BackupProfile) -> bool:
            return self._run(lambda: self._service.delete_backup_profile(profile.id))

        return ProfileListPanel[BackupProfile](
            column_labels=("الاسم", "الفترة (ساعة)", "عدد النسخ", "موقع التخزين"),
            row_values=lambda p: (
                p.name,
                str(p.interval_hours),
                str(p.retention_count),
                p.location_type.value,
            ),
            list_items=self._service.list_backup_profiles,
            on_add=on_add,
            on_edit=on_edit,
            on_delete=on_delete,
            add_label="نمط نسخ احتياطي جديد",
            edit_label="تعديل",
            delete_label="حذف",
            delete_confirm_text=lambda p: f"هل تريد حذف نمط النسخ الاحتياطي «{p.name}»؟",
            parent=self,
        )

    # -- Configuration bundles tab ----------------------------------------------

    def _build_configuration_tab(self) -> ProfileListPanel[RemoteConfiguration]:
        def open_dialog(existing: RemoteConfiguration | None) -> RemoteConfigurationDialog | None:
            theme_profiles = self._service.list_theme_profiles()
            print_profiles = self._service.list_print_profiles()
            attendance_policy_profiles = self._service.list_attendance_policy_profiles()
            device_profiles = self._service.list_device_profiles()
            backup_profiles = self._service.list_backup_profiles()
            if not all(
                (theme_profiles, print_profiles, attendance_policy_profiles, device_profiles, backup_profiles)
            ):
                QMessageBox.information(
                    self,
                    "حزمة إعدادات",
                    "الرجاء إنشاء نمط واحد على الأقل من كل نوع أولاً.",
                )
                return None
            return RemoteConfigurationDialog(
                theme_profiles=theme_profiles,
                print_profiles=print_profiles,
                attendance_policy_profiles=attendance_policy_profiles,
                device_profiles=device_profiles,
                backup_profiles=backup_profiles,
                existing=existing,
                parent=self,
            )

        def on_add() -> bool:
            dialog = open_dialog(None)
            if dialog is None or dialog.exec() != RemoteConfigurationDialog.DialogCode.Accepted:
                return False
            return self._run(lambda: self._service.create_configuration(**dialog.field_values()))

        def on_edit(configuration: RemoteConfiguration) -> bool:
            dialog = open_dialog(configuration)
            if dialog is None or dialog.exec() != RemoteConfigurationDialog.DialogCode.Accepted:
                return False
            return self._run(
                lambda: self._service.update_configuration(configuration.id, **dialog.field_values())
            )

        def on_delete(configuration: RemoteConfiguration) -> bool:
            return self._run(lambda: self._service.delete_configuration(configuration.id))

        return ProfileListPanel[RemoteConfiguration](
            column_labels=("الاسم", "الإصدار", "نمط المظهر", "سياسة الحضور"),
            row_values=lambda c: (
                c.name,
                str(c.version),
                c.theme_profile.name,
                c.attendance_policy_profile.name,
            ),
            list_items=self._service.list_configurations,
            on_add=on_add,
            on_edit=on_edit,
            on_delete=on_delete,
            add_label="حزمة إعدادات جديدة",
            edit_label="تعديل",
            delete_label="حذف",
            delete_confirm_text=lambda c: f"هل تريد حذف حزمة الإعدادات «{c.name}»؟",
            parent=self,
        )

    def _run(self, action) -> bool:
        """Run ``action``, showing a warning dialog instead of raising on failure.

        Args:
            action: A zero-argument callable performing one service
                operation.

        Returns:
            ``True`` if ``action`` succeeded (the caller should
            reload), ``False`` if it raised
            :class:`~developer_suite.services.configuration_service.ConfigurationServiceError`.
        """
        try:
            action()
        except ConfigurationServiceError as exc:
            QMessageBox.warning(self, "تعذّرت العملية", str(exc))
            return False
        return True

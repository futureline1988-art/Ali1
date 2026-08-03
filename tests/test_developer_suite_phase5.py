"""Tests for Phase 5 of the commercial platform work: Remote Configuration
foundation inside the Developer Suite.

Every test here exercises only :mod:`developer_suite`; nothing touches
the Attendance Client's own database, config, or models, and nothing
here performs network I/O, synchronization, or communicates with any
customer application — this phase only defines how these templates are
stored and edited.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import developer_suite.config as developer_suite_config_module
from developer_suite.config import DeveloperSuiteConfig, get_developer_suite_config
from developer_suite.database.bootstrap import build_database
from developer_suite.models.attendance_policy_profile import AttendancePolicyProfile
from developer_suite.models.backup_profile import BackupLocationType, BackupProfile
from developer_suite.models.device_profile import DeviceProfile
from developer_suite.models.print_profile import PaperSize, PrintProfile
from developer_suite.models.remote_configuration import RemoteConfiguration
from developer_suite.models.theme_profile import ThemeMode, ThemeProfile
from developer_suite.repositories.configuration_repository import ConfigurationRepository
from developer_suite.services.configuration_service import (
    ConfigurationNotFoundError,
    ConfigurationService,
    ConfigurationValidationError,
    ProfileNotFoundError,
)
from developer_suite.ui.configuration_editor_page import ConfigurationEditorPage
from developer_suite.ui.profile_dialogs import (
    AttendancePolicyProfileDialog,
    BackupProfileDialog,
    DeviceProfileDialog,
    PrintProfileDialog,
    ThemeProfileDialog,
)
from developer_suite.ui.remote_configuration_dialog import RemoteConfigurationDialog
from models.enums import DeviceProtocol, Weekday


@pytest.fixture
def dev_suite_config(tmp_path, monkeypatch) -> DeveloperSuiteConfig:
    monkeypatch.setenv("DEV_SUITE_DB_SQLITE_PATH", str(tmp_path / "developer_suite_test.db"))
    developer_suite_config_module._config_instance = None
    yield get_developer_suite_config()
    developer_suite_config_module._config_instance = None


@pytest.fixture
def dev_suite_database(dev_suite_config):
    database = build_database(dev_suite_config)
    yield database
    database.dispose()


@pytest.fixture
def configuration_service(dev_suite_database) -> ConfigurationService:
    return ConfigurationService(dev_suite_database)


@pytest.fixture
def bundle_profiles(configuration_service: ConfigurationService):
    """One of each profile type, ready to compose into a bundle."""
    return {
        "theme": configuration_service.create_theme_profile(name="Default Theme"),
        "print": configuration_service.create_print_profile(name="Default Print"),
        "attendance_policy": configuration_service.create_attendance_policy_profile(
            name="Default Policy"
        ),
        "device": configuration_service.create_device_profile(name="Default Device"),
        "backup": configuration_service.create_backup_profile(name="Default Backup"),
    }


class TestThemeProfileService:
    def test_create_uses_defaults(self, configuration_service: ConfigurationService) -> None:
        profile = configuration_service.create_theme_profile(name="Default Theme")
        assert profile.mode is ThemeMode.LIGHT
        assert profile.primary_color == "#1976D2"
        assert profile.font_family == "Cairo"

    def test_create_rejects_short_name(self, configuration_service: ConfigurationService) -> None:
        with pytest.raises(ConfigurationValidationError):
            configuration_service.create_theme_profile(name="A")

    def test_update_changes_fields(self, configuration_service: ConfigurationService) -> None:
        profile = configuration_service.create_theme_profile(name="Default Theme")
        updated = configuration_service.update_theme_profile(
            profile.id,
            name="Dark Theme",
            mode=ThemeMode.DARK,
            primary_color="#000000",
            secondary_color="#111111",
        )
        assert updated.name == "Dark Theme"
        assert updated.mode is ThemeMode.DARK

    def test_update_raises_for_unknown_id(self, configuration_service: ConfigurationService) -> None:
        with pytest.raises(ProfileNotFoundError):
            configuration_service.update_theme_profile(
                999999,
                name="XX",
                mode=ThemeMode.LIGHT,
                primary_color="#000000",
                secondary_color="#111111",
            )

    def test_delete_removes_from_list(self, configuration_service: ConfigurationService) -> None:
        profile = configuration_service.create_theme_profile(name="Default Theme")
        configuration_service.delete_theme_profile(profile.id)
        assert configuration_service.get_theme_profile(profile.id) is None

    def test_delete_raises_for_unknown_id(self, configuration_service: ConfigurationService) -> None:
        with pytest.raises(ProfileNotFoundError):
            configuration_service.delete_theme_profile(999999)

    def test_list_returns_created_profiles(self, configuration_service: ConfigurationService) -> None:
        configuration_service.create_theme_profile(name="Theme A")
        configuration_service.create_theme_profile(name="Theme B")
        assert len(configuration_service.list_theme_profiles()) == 2


class TestPrintProfileService:
    def test_create_uses_defaults(self, configuration_service: ConfigurationService) -> None:
        profile = configuration_service.create_print_profile(name="Default Print")
        assert profile.paper_size is PaperSize.A4
        assert profile.margin_mm == 15
        assert profile.show_company_logo is True

    def test_update_changes_fields(self, configuration_service: ConfigurationService) -> None:
        profile = configuration_service.create_print_profile(name="Default Print")
        updated = configuration_service.update_print_profile(
            profile.id,
            name="Thermal Print",
            paper_size=PaperSize.THERMAL_80MM,
            show_company_logo=False,
            show_qr_code=False,
            margin_mm=5,
        )
        assert updated.paper_size is PaperSize.THERMAL_80MM
        assert updated.margin_mm == 5

    def test_delete_raises_for_unknown_id(self, configuration_service: ConfigurationService) -> None:
        with pytest.raises(ProfileNotFoundError):
            configuration_service.delete_print_profile(999999)


class TestAttendancePolicyProfileService:
    def test_create_uses_default_working_days(self, configuration_service: ConfigurationService) -> None:
        profile = configuration_service.create_attendance_policy_profile(name="Default Policy")
        assert Weekday.FRIDAY.value not in profile.working_days
        assert Weekday.SATURDAY.value in profile.working_days

    def test_create_rejects_invalid_weekday_code(
        self, configuration_service: ConfigurationService
    ) -> None:
        with pytest.raises(ConfigurationValidationError):
            configuration_service.create_attendance_policy_profile(
                name="Bad Policy", working_days=["not-a-day"]
            )

    def test_update_changes_working_days(self, configuration_service: ConfigurationService) -> None:
        profile = configuration_service.create_attendance_policy_profile(name="Default Policy")
        updated = configuration_service.update_attendance_policy_profile(
            profile.id,
            grace_period_minutes=10,
            early_leave_grace_minutes=5,
            overtime_threshold_minutes=30,
            half_day_threshold_hours=4,
            working_days=[Weekday.FRIDAY.value],
            name="Friday Only",
        )
        assert updated.working_days == [Weekday.FRIDAY.value]

    def test_update_rejects_invalid_weekday_code(
        self, configuration_service: ConfigurationService
    ) -> None:
        profile = configuration_service.create_attendance_policy_profile(name="Default Policy")
        with pytest.raises(ConfigurationValidationError):
            configuration_service.update_attendance_policy_profile(
                profile.id,
                name="Default Policy",
                grace_period_minutes=0,
                early_leave_grace_minutes=0,
                overtime_threshold_minutes=0,
                half_day_threshold_hours=4,
                working_days=["bogus"],
            )


class TestDeviceProfileService:
    def test_create_uses_defaults(self, configuration_service: ConfigurationService) -> None:
        profile = configuration_service.create_device_profile(name="Default Device")
        assert profile.protocol is DeviceProtocol.ZKTECO_TCP
        assert profile.default_port == 4370

    def test_update_changes_protocol(self, configuration_service: ConfigurationService) -> None:
        profile = configuration_service.create_device_profile(name="Default Device")
        updated = configuration_service.update_device_profile(
            profile.id,
            name="Hikvision Device",
            protocol=DeviceProtocol.HIKVISION,
            default_port=8000,
            timeout_seconds=10,
            sync_interval_minutes=30,
            auto_reconnect=False,
        )
        assert updated.protocol is DeviceProtocol.HIKVISION
        assert updated.auto_reconnect is False

    def test_delete_raises_for_unknown_id(self, configuration_service: ConfigurationService) -> None:
        with pytest.raises(ProfileNotFoundError):
            configuration_service.delete_device_profile(999999)


class TestBackupProfileService:
    def test_create_uses_defaults(self, configuration_service: ConfigurationService) -> None:
        profile = configuration_service.create_backup_profile(name="Default Backup")
        assert profile.location_type is BackupLocationType.LOCAL
        assert profile.encrypt_backups is True

    def test_update_changes_location(self, configuration_service: ConfigurationService) -> None:
        profile = configuration_service.create_backup_profile(name="Default Backup")
        updated = configuration_service.update_backup_profile(
            profile.id,
            name="Cloud Backup",
            enabled=True,
            interval_hours=12,
            retention_count=30,
            location_type=BackupLocationType.CLOUD,
            encrypt_backups=True,
        )
        assert updated.location_type is BackupLocationType.CLOUD
        assert updated.interval_hours == 12

    def test_delete_raises_for_unknown_id(self, configuration_service: ConfigurationService) -> None:
        with pytest.raises(ProfileNotFoundError):
            configuration_service.delete_backup_profile(999999)


class TestConfigurationBundleService:
    def test_create_composes_five_profiles(
        self, configuration_service: ConfigurationService, bundle_profiles
    ) -> None:
        bundle = configuration_service.create_configuration(
            name="Default Bundle",
            theme_profile_id=bundle_profiles["theme"].id,
            print_profile_id=bundle_profiles["print"].id,
            attendance_policy_profile_id=bundle_profiles["attendance_policy"].id,
            device_profile_id=bundle_profiles["device"].id,
            backup_profile_id=bundle_profiles["backup"].id,
        )
        assert bundle.version == 1
        # Accessed after the session that created it is closed - proves eager loading.
        assert bundle.theme_profile.name == "Default Theme"
        assert bundle.backup_profile.name == "Default Backup"

    def test_create_raises_for_unknown_profile(
        self, configuration_service: ConfigurationService, bundle_profiles
    ) -> None:
        with pytest.raises(ProfileNotFoundError):
            configuration_service.create_configuration(
                name="Broken Bundle",
                theme_profile_id=999999,
                print_profile_id=bundle_profiles["print"].id,
                attendance_policy_profile_id=bundle_profiles["attendance_policy"].id,
                device_profile_id=bundle_profiles["device"].id,
                backup_profile_id=bundle_profiles["backup"].id,
            )

    def test_update_bumps_version_and_reassigns_profile(
        self, configuration_service: ConfigurationService, bundle_profiles
    ) -> None:
        bundle = configuration_service.create_configuration(
            name="Default Bundle",
            theme_profile_id=bundle_profiles["theme"].id,
            print_profile_id=bundle_profiles["print"].id,
            attendance_policy_profile_id=bundle_profiles["attendance_policy"].id,
            device_profile_id=bundle_profiles["device"].id,
            backup_profile_id=bundle_profiles["backup"].id,
        )
        new_theme = configuration_service.create_theme_profile(name="Dark Theme", mode=ThemeMode.DARK)

        updated = configuration_service.update_configuration(
            bundle.id,
            name="Default Bundle",
            theme_profile_id=new_theme.id,
            print_profile_id=bundle_profiles["print"].id,
            attendance_policy_profile_id=bundle_profiles["attendance_policy"].id,
            device_profile_id=bundle_profiles["device"].id,
            backup_profile_id=bundle_profiles["backup"].id,
        )
        assert updated.version == 2
        assert updated.theme_profile.name == "Dark Theme"

    def test_update_raises_for_unknown_bundle(
        self, configuration_service: ConfigurationService, bundle_profiles
    ) -> None:
        with pytest.raises(ConfigurationNotFoundError):
            configuration_service.update_configuration(
                999999,
                name="XX",
                theme_profile_id=bundle_profiles["theme"].id,
                print_profile_id=bundle_profiles["print"].id,
                attendance_policy_profile_id=bundle_profiles["attendance_policy"].id,
                device_profile_id=bundle_profiles["device"].id,
                backup_profile_id=bundle_profiles["backup"].id,
            )

    def test_delete_removes_from_list(
        self, configuration_service: ConfigurationService, bundle_profiles
    ) -> None:
        bundle = configuration_service.create_configuration(
            name="Default Bundle",
            theme_profile_id=bundle_profiles["theme"].id,
            print_profile_id=bundle_profiles["print"].id,
            attendance_policy_profile_id=bundle_profiles["attendance_policy"].id,
            device_profile_id=bundle_profiles["device"].id,
            backup_profile_id=bundle_profiles["backup"].id,
        )
        configuration_service.delete_configuration(bundle.id)
        assert configuration_service.get_configuration(bundle.id) is None

    def test_delete_raises_for_unknown_id(self, configuration_service: ConfigurationService) -> None:
        with pytest.raises(ConfigurationNotFoundError):
            configuration_service.delete_configuration(999999)

    def test_list_orders_by_name(
        self, configuration_service: ConfigurationService, bundle_profiles
    ) -> None:
        configuration_service.create_configuration(
            name="Zeta Bundle",
            theme_profile_id=bundle_profiles["theme"].id,
            print_profile_id=bundle_profiles["print"].id,
            attendance_policy_profile_id=bundle_profiles["attendance_policy"].id,
            device_profile_id=bundle_profiles["device"].id,
            backup_profile_id=bundle_profiles["backup"].id,
        )
        configuration_service.create_configuration(
            name="Alpha Bundle",
            theme_profile_id=bundle_profiles["theme"].id,
            print_profile_id=bundle_profiles["print"].id,
            attendance_policy_profile_id=bundle_profiles["attendance_policy"].id,
            device_profile_id=bundle_profiles["device"].id,
            backup_profile_id=bundle_profiles["backup"].id,
        )
        names = [bundle.name for bundle in configuration_service.list_configurations()]
        assert names == ["Alpha Bundle", "Zeta Bundle"]


class TestConfigurationRepository:
    def test_soft_deleted_bundle_excluded_from_list(
        self, dev_suite_database, configuration_service: ConfigurationService, bundle_profiles
    ) -> None:
        bundle = configuration_service.create_configuration(
            name="Default Bundle",
            theme_profile_id=bundle_profiles["theme"].id,
            print_profile_id=bundle_profiles["print"].id,
            attendance_policy_profile_id=bundle_profiles["attendance_policy"].id,
            device_profile_id=bundle_profiles["device"].id,
            backup_profile_id=bundle_profiles["backup"].id,
        )
        with dev_suite_database.session_scope() as session:
            repo = ConfigurationRepository(session)
            configuration = repo.get_configuration(bundle.id)
            repo.delete_configuration(configuration)

        with dev_suite_database.session_scope() as session:
            assert ConfigurationRepository(session).list_configurations() == []


class TestProfileDialogs:
    def test_theme_dialog_defaults(self, qapp) -> None:
        dialog = ThemeProfileDialog(existing=None)
        values = dialog.field_values()
        assert values["mode"] is ThemeMode.LIGHT
        assert values["primary_color"] == "#1976D2"

    def test_theme_dialog_prefills_from_existing(
        self, qapp, configuration_service: ConfigurationService
    ) -> None:
        profile = configuration_service.create_theme_profile(name="Dark Theme", mode=ThemeMode.DARK)
        dialog = ThemeProfileDialog(existing=profile)
        values = dialog.field_values()
        assert values["name"] == "Dark Theme"
        assert values["mode"] is ThemeMode.DARK

    def test_print_dialog_defaults(self, qapp) -> None:
        dialog = PrintProfileDialog(existing=None)
        values = dialog.field_values()
        assert values["paper_size"] is PaperSize.A4
        assert values["margin_mm"] == 15

    def test_attendance_policy_dialog_defaults_exclude_friday(self, qapp) -> None:
        dialog = AttendancePolicyProfileDialog(existing=None)
        values = dialog.field_values()
        assert Weekday.FRIDAY.value not in values["working_days"]
        assert Weekday.SATURDAY.value in values["working_days"]

    def test_attendance_policy_dialog_prefills_working_days(
        self, qapp, configuration_service: ConfigurationService
    ) -> None:
        profile = configuration_service.create_attendance_policy_profile(
            name="Friday Only", working_days=[Weekday.FRIDAY.value]
        )
        dialog = AttendancePolicyProfileDialog(existing=profile)
        assert dialog._working_day_checks[Weekday.FRIDAY].isChecked() is True
        assert dialog._working_day_checks[Weekday.SATURDAY].isChecked() is False

    def test_device_dialog_defaults(self, qapp) -> None:
        dialog = DeviceProfileDialog(existing=None)
        values = dialog.field_values()
        assert values["protocol"] is DeviceProtocol.ZKTECO_TCP
        assert values["default_port"] == 4370

    def test_backup_dialog_defaults(self, qapp) -> None:
        dialog = BackupProfileDialog(existing=None)
        values = dialog.field_values()
        assert values["location_type"] is BackupLocationType.LOCAL
        assert values["encrypt_backups"] is True

    def test_remote_configuration_dialog_defaults_to_first_profile(
        self, qapp, bundle_profiles
    ) -> None:
        dialog = RemoteConfigurationDialog(
            theme_profiles=[bundle_profiles["theme"]],
            print_profiles=[bundle_profiles["print"]],
            attendance_policy_profiles=[bundle_profiles["attendance_policy"]],
            device_profiles=[bundle_profiles["device"]],
            backup_profiles=[bundle_profiles["backup"]],
            existing=None,
        )
        values = dialog.field_values()
        assert values["theme_profile_id"] == bundle_profiles["theme"].id
        assert values["description"] is None

    def test_remote_configuration_dialog_prefills_from_existing(
        self, qapp, configuration_service: ConfigurationService, bundle_profiles
    ) -> None:
        bundle = configuration_service.create_configuration(
            name="Default Bundle",
            description="ملاحظة",
            theme_profile_id=bundle_profiles["theme"].id,
            print_profile_id=bundle_profiles["print"].id,
            attendance_policy_profile_id=bundle_profiles["attendance_policy"].id,
            device_profile_id=bundle_profiles["device"].id,
            backup_profile_id=bundle_profiles["backup"].id,
        )
        dialog = RemoteConfigurationDialog(
            theme_profiles=[bundle_profiles["theme"]],
            print_profiles=[bundle_profiles["print"]],
            attendance_policy_profiles=[bundle_profiles["attendance_policy"]],
            device_profiles=[bundle_profiles["device"]],
            backup_profiles=[bundle_profiles["backup"]],
            existing=bundle,
        )
        values = dialog.field_values()
        assert values["name"] == "Default Bundle"
        assert values["description"] == "ملاحظة"
        assert values["theme_profile_id"] == bundle_profiles["theme"].id


class TestConfigurationEditorPage:
    def test_has_six_tabs(self, qapp, configuration_service: ConfigurationService) -> None:
        page = ConfigurationEditorPage(configuration_service)
        assert page.tabs.count() == 6

    def test_theme_tab_loads_existing_profiles(
        self, qapp, configuration_service: ConfigurationService
    ) -> None:
        configuration_service.create_theme_profile(name="Theme A")
        configuration_service.create_theme_profile(name="Theme B")
        page = ConfigurationEditorPage(configuration_service)
        assert page.theme_panel.table.rowCount() == 2

    def test_configuration_tab_loads_existing_bundles(
        self, qapp, configuration_service: ConfigurationService, bundle_profiles
    ) -> None:
        configuration_service.create_configuration(
            name="Default Bundle",
            theme_profile_id=bundle_profiles["theme"].id,
            print_profile_id=bundle_profiles["print"].id,
            attendance_policy_profile_id=bundle_profiles["attendance_policy"].id,
            device_profile_id=bundle_profiles["device"].id,
            backup_profile_id=bundle_profiles["backup"].id,
        )
        page = ConfigurationEditorPage(configuration_service)
        assert page.configuration_panel.table.rowCount() == 1
        assert page.configuration_panel.table.item(0, 0).text() == "Default Bundle"


class TestZeroImpactOnAttendanceClient:
    def test_configuration_tables_live_only_in_developer_suite_schema(self) -> None:
        from developer_suite.database.base import Base as DeveloperSuiteBase
        from models.base import Base as AttendanceBase

        dev_suite_tables = DeveloperSuiteBase.metadata.tables
        attendance_tables = AttendanceBase.metadata.tables
        for table_name in (
            "theme_profiles",
            "print_profiles",
            "attendance_policy_profiles",
            "device_profiles",
            "backup_profiles",
            "remote_configurations",
        ):
            assert table_name in dev_suite_tables
            assert table_name not in attendance_tables

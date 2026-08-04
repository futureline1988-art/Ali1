"""Remote Configuration business logic (Phase 5: storage and editing only).

One service for the whole feature area, mirroring
:mod:`developer_suite.repositories.configuration_repository`'s "one
repository for six models" shape: every profile type gets the same
small create/update/delete/list/get group, and
:class:`~developer_suite.models.remote_configuration.RemoteConfiguration`
composes five of them into a named bundle. Nothing here talks to a
network, a scheduler, or any customer application — see
``developer_suite/modules/remote_configuration.py``.
"""

from __future__ import annotations

from typing import NamedTuple

from developer_suite.models.attendance_policy_profile import AttendancePolicyProfile
from developer_suite.models.backup_profile import BackupLocationType, BackupProfile
from developer_suite.models.device_profile import DeviceProfile
from developer_suite.models.print_profile import PaperSize, PrintProfile
from developer_suite.models.remote_configuration import RemoteConfiguration
from developer_suite.models.theme_profile import ThemeMode, ThemeProfile
from developer_suite.repositories.configuration_repository import ConfigurationRepository
from developer_suite.services.base_service import BaseService
from models.enums import DeviceProtocol, Weekday
from utils.validators import is_within_length


class ConfigurationServiceError(Exception):
    """Base class for Remote Configuration operation failures the UI should display."""


class ConfigurationValidationError(ConfigurationServiceError):
    """A field failed validation."""


class ProfileNotFoundError(ConfigurationServiceError):
    """No profile of the requested type exists with the given id."""


class ConfigurationNotFoundError(ConfigurationServiceError):
    """No configuration bundle exists with the given id."""


class _RequiredProfiles(NamedTuple):
    """The five profiles a configuration bundle references, already loaded."""

    theme_profile: ThemeProfile
    print_profile: PrintProfile
    attendance_policy_profile: AttendancePolicyProfile
    device_profile: DeviceProfile
    backup_profile: BackupProfile


def _validate_name(name: str) -> str:
    """Validate and normalize a profile/bundle name shared by every entity here.

    Raises:
        ConfigurationValidationError: ``name`` is not 2-150 characters.
    """
    if not is_within_length(name, minimum=2, maximum=150):
        raise ConfigurationValidationError("Name must be 2-150 characters.")
    return name.strip()


class ConfigurationService(BaseService):
    """Create, update, delete, list, and get every Remote Configuration entity."""

    # -- Theme profiles ---------------------------------------------------

    def create_theme_profile(
        self,
        *,
        name: str,
        mode: ThemeMode = ThemeMode.LIGHT,
        primary_color: str = "#1976D2",
        secondary_color: str = "#424242",
        accent_color: str | None = None,
        logo_path: str | None = None,
        font_family: str = "Cairo",
        language: str = "ar",
    ) -> ThemeProfile:
        """Create a new theme profile.

        Raises:
            ConfigurationValidationError: ``name`` fails validation.
        """
        validated_name = _validate_name(name)
        with self._session_scope() as session:
            profile = ThemeProfile(
                name=validated_name,
                mode=mode,
                primary_color=primary_color,
                secondary_color=secondary_color,
                accent_color=accent_color,
                logo_path=logo_path,
                font_family=font_family,
                language=language,
            )
            return ConfigurationRepository(session).theme_profiles.add(profile)

    def update_theme_profile(
        self,
        profile_id: int,
        *,
        name: str,
        mode: ThemeMode,
        primary_color: str,
        secondary_color: str,
        accent_color: str | None = None,
        logo_path: str | None = None,
        font_family: str = "Cairo",
        language: str = "ar",
    ) -> ThemeProfile:
        """Update an existing theme profile.

        Raises:
            ConfigurationValidationError: ``name`` fails validation.
            ProfileNotFoundError: No theme profile exists with that id.
        """
        validated_name = _validate_name(name)
        with self._session_scope() as session:
            profile = ConfigurationRepository(session).theme_profiles.get_by_id(profile_id)
            if profile is None:
                raise ProfileNotFoundError(f"No theme profile with id={profile_id!r}.")
            profile.name = validated_name
            profile.mode = mode
            profile.primary_color = primary_color
            profile.secondary_color = secondary_color
            profile.accent_color = accent_color
            profile.logo_path = logo_path
            profile.font_family = font_family
            profile.language = language
            session.flush()
            return profile

    def delete_theme_profile(self, profile_id: int) -> None:
        """Soft-delete a theme profile.

        Raises:
            ProfileNotFoundError: No theme profile exists with that id.
        """
        with self._session_scope() as session:
            repo = ConfigurationRepository(session).theme_profiles
            profile = repo.get_by_id(profile_id)
            if profile is None:
                raise ProfileNotFoundError(f"No theme profile with id={profile_id!r}.")
            repo.delete(profile)

    def list_theme_profiles(self) -> list[ThemeProfile]:
        """List every theme profile, ordered by id."""
        with self._session_scope() as session:
            return ConfigurationRepository(session).theme_profiles.list_all()

    def get_theme_profile(self, profile_id: int) -> ThemeProfile | None:
        """Fetch a single theme profile by id, or ``None`` if not found."""
        with self._session_scope() as session:
            return ConfigurationRepository(session).theme_profiles.get_by_id(profile_id)

    # -- Print profiles -----------------------------------------------------

    def create_print_profile(
        self,
        *,
        name: str,
        paper_size: PaperSize = PaperSize.A4,
        header_text: str | None = None,
        footer_text: str | None = None,
        show_company_logo: bool = True,
        show_qr_code: bool = True,
        margin_mm: int = 15,
    ) -> PrintProfile:
        """Create a new print profile.

        Raises:
            ConfigurationValidationError: ``name`` fails validation.
        """
        validated_name = _validate_name(name)
        with self._session_scope() as session:
            profile = PrintProfile(
                name=validated_name,
                paper_size=paper_size,
                header_text=header_text,
                footer_text=footer_text,
                show_company_logo=show_company_logo,
                show_qr_code=show_qr_code,
                margin_mm=margin_mm,
            )
            return ConfigurationRepository(session).print_profiles.add(profile)

    def update_print_profile(
        self,
        profile_id: int,
        *,
        name: str,
        paper_size: PaperSize,
        header_text: str | None = None,
        footer_text: str | None = None,
        show_company_logo: bool = True,
        show_qr_code: bool = True,
        margin_mm: int = 15,
    ) -> PrintProfile:
        """Update an existing print profile.

        Raises:
            ConfigurationValidationError: ``name`` fails validation.
            ProfileNotFoundError: No print profile exists with that id.
        """
        validated_name = _validate_name(name)
        with self._session_scope() as session:
            profile = ConfigurationRepository(session).print_profiles.get_by_id(profile_id)
            if profile is None:
                raise ProfileNotFoundError(f"No print profile with id={profile_id!r}.")
            profile.name = validated_name
            profile.paper_size = paper_size
            profile.header_text = header_text
            profile.footer_text = footer_text
            profile.show_company_logo = show_company_logo
            profile.show_qr_code = show_qr_code
            profile.margin_mm = margin_mm
            session.flush()
            return profile

    def delete_print_profile(self, profile_id: int) -> None:
        """Soft-delete a print profile.

        Raises:
            ProfileNotFoundError: No print profile exists with that id.
        """
        with self._session_scope() as session:
            repo = ConfigurationRepository(session).print_profiles
            profile = repo.get_by_id(profile_id)
            if profile is None:
                raise ProfileNotFoundError(f"No print profile with id={profile_id!r}.")
            repo.delete(profile)

    def list_print_profiles(self) -> list[PrintProfile]:
        """List every print profile, ordered by id."""
        with self._session_scope() as session:
            return ConfigurationRepository(session).print_profiles.list_all()

    def get_print_profile(self, profile_id: int) -> PrintProfile | None:
        """Fetch a single print profile by id, or ``None`` if not found."""
        with self._session_scope() as session:
            return ConfigurationRepository(session).print_profiles.get_by_id(profile_id)

    # -- Attendance policy profiles ------------------------------------------

    def _validate_working_days(self, working_days: list[str]) -> list[str]:
        """Validate every entry is a real :class:`~models.enums.Weekday` code.

        Raises:
            ConfigurationValidationError: Any entry is not a valid
                weekday code.
        """
        valid_codes = {weekday.value for weekday in Weekday}
        invalid = [day for day in working_days if day not in valid_codes]
        if invalid:
            raise ConfigurationValidationError(f"Invalid weekday code(s): {invalid!r}.")
        return list(working_days)

    def create_attendance_policy_profile(
        self,
        *,
        name: str,
        grace_period_minutes: int = 0,
        early_leave_grace_minutes: int = 0,
        overtime_threshold_minutes: int = 0,
        half_day_threshold_hours: int = 4,
        working_days: list[str] | None = None,
    ) -> AttendancePolicyProfile:
        """Create a new attendance policy profile.

        Raises:
            ConfigurationValidationError: ``name`` or ``working_days``
                fails validation.
        """
        validated_name = _validate_name(name)
        kwargs: dict[str, object] = {}
        if working_days is not None:
            kwargs["working_days"] = self._validate_working_days(working_days)
        with self._session_scope() as session:
            profile = AttendancePolicyProfile(
                name=validated_name,
                grace_period_minutes=grace_period_minutes,
                early_leave_grace_minutes=early_leave_grace_minutes,
                overtime_threshold_minutes=overtime_threshold_minutes,
                half_day_threshold_hours=half_day_threshold_hours,
                **kwargs,
            )
            return ConfigurationRepository(session).attendance_policy_profiles.add(profile)

    def update_attendance_policy_profile(
        self,
        profile_id: int,
        *,
        name: str,
        grace_period_minutes: int,
        early_leave_grace_minutes: int,
        overtime_threshold_minutes: int,
        half_day_threshold_hours: int,
        working_days: list[str],
    ) -> AttendancePolicyProfile:
        """Update an existing attendance policy profile.

        Raises:
            ConfigurationValidationError: ``name`` or ``working_days``
                fails validation.
            ProfileNotFoundError: No profile exists with that id.
        """
        validated_name = _validate_name(name)
        validated_days = self._validate_working_days(working_days)
        with self._session_scope() as session:
            profile = ConfigurationRepository(session).attendance_policy_profiles.get_by_id(
                profile_id
            )
            if profile is None:
                raise ProfileNotFoundError(f"No attendance policy profile with id={profile_id!r}.")
            profile.name = validated_name
            profile.grace_period_minutes = grace_period_minutes
            profile.early_leave_grace_minutes = early_leave_grace_minutes
            profile.overtime_threshold_minutes = overtime_threshold_minutes
            profile.half_day_threshold_hours = half_day_threshold_hours
            profile.working_days = validated_days
            session.flush()
            return profile

    def delete_attendance_policy_profile(self, profile_id: int) -> None:
        """Soft-delete an attendance policy profile.

        Raises:
            ProfileNotFoundError: No profile exists with that id.
        """
        with self._session_scope() as session:
            repo = ConfigurationRepository(session).attendance_policy_profiles
            profile = repo.get_by_id(profile_id)
            if profile is None:
                raise ProfileNotFoundError(f"No attendance policy profile with id={profile_id!r}.")
            repo.delete(profile)

    def list_attendance_policy_profiles(self) -> list[AttendancePolicyProfile]:
        """List every attendance policy profile, ordered by id."""
        with self._session_scope() as session:
            return ConfigurationRepository(session).attendance_policy_profiles.list_all()

    def get_attendance_policy_profile(self, profile_id: int) -> AttendancePolicyProfile | None:
        """Fetch a single attendance policy profile by id, or ``None`` if not found."""
        with self._session_scope() as session:
            return ConfigurationRepository(session).attendance_policy_profiles.get_by_id(profile_id)

    # -- Device profiles ------------------------------------------------------

    def create_device_profile(
        self,
        *,
        name: str,
        protocol: DeviceProtocol = DeviceProtocol.ZKTECO_TCP,
        default_port: int = 4370,
        timeout_seconds: int = 8,
        sync_interval_minutes: int = 15,
        auto_reconnect: bool = True,
    ) -> DeviceProfile:
        """Create a new device profile.

        Raises:
            ConfigurationValidationError: ``name`` fails validation.
        """
        validated_name = _validate_name(name)
        with self._session_scope() as session:
            profile = DeviceProfile(
                name=validated_name,
                protocol=protocol,
                default_port=default_port,
                timeout_seconds=timeout_seconds,
                sync_interval_minutes=sync_interval_minutes,
                auto_reconnect=auto_reconnect,
            )
            return ConfigurationRepository(session).device_profiles.add(profile)

    def update_device_profile(
        self,
        profile_id: int,
        *,
        name: str,
        protocol: DeviceProtocol,
        default_port: int,
        timeout_seconds: int,
        sync_interval_minutes: int,
        auto_reconnect: bool,
    ) -> DeviceProfile:
        """Update an existing device profile.

        Raises:
            ConfigurationValidationError: ``name`` fails validation.
            ProfileNotFoundError: No device profile exists with that id.
        """
        validated_name = _validate_name(name)
        with self._session_scope() as session:
            profile = ConfigurationRepository(session).device_profiles.get_by_id(profile_id)
            if profile is None:
                raise ProfileNotFoundError(f"No device profile with id={profile_id!r}.")
            profile.name = validated_name
            profile.protocol = protocol
            profile.default_port = default_port
            profile.timeout_seconds = timeout_seconds
            profile.sync_interval_minutes = sync_interval_minutes
            profile.auto_reconnect = auto_reconnect
            session.flush()
            return profile

    def delete_device_profile(self, profile_id: int) -> None:
        """Soft-delete a device profile.

        Raises:
            ProfileNotFoundError: No device profile exists with that id.
        """
        with self._session_scope() as session:
            repo = ConfigurationRepository(session).device_profiles
            profile = repo.get_by_id(profile_id)
            if profile is None:
                raise ProfileNotFoundError(f"No device profile with id={profile_id!r}.")
            repo.delete(profile)

    def list_device_profiles(self) -> list[DeviceProfile]:
        """List every device profile, ordered by id."""
        with self._session_scope() as session:
            return ConfigurationRepository(session).device_profiles.list_all()

    def get_device_profile(self, profile_id: int) -> DeviceProfile | None:
        """Fetch a single device profile by id, or ``None`` if not found."""
        with self._session_scope() as session:
            return ConfigurationRepository(session).device_profiles.get_by_id(profile_id)

    # -- Backup profiles --------------------------------------------------

    def create_backup_profile(
        self,
        *,
        name: str,
        enabled: bool = True,
        interval_hours: int = 24,
        retention_count: int = 14,
        location_type: BackupLocationType = BackupLocationType.LOCAL,
        encrypt_backups: bool = True,
    ) -> BackupProfile:
        """Create a new backup profile.

        Raises:
            ConfigurationValidationError: ``name`` fails validation.
        """
        validated_name = _validate_name(name)
        with self._session_scope() as session:
            profile = BackupProfile(
                name=validated_name,
                enabled=enabled,
                interval_hours=interval_hours,
                retention_count=retention_count,
                location_type=location_type,
                encrypt_backups=encrypt_backups,
            )
            return ConfigurationRepository(session).backup_profiles.add(profile)

    def update_backup_profile(
        self,
        profile_id: int,
        *,
        name: str,
        enabled: bool,
        interval_hours: int,
        retention_count: int,
        location_type: BackupLocationType,
        encrypt_backups: bool,
    ) -> BackupProfile:
        """Update an existing backup profile.

        Raises:
            ConfigurationValidationError: ``name`` fails validation.
            ProfileNotFoundError: No backup profile exists with that id.
        """
        validated_name = _validate_name(name)
        with self._session_scope() as session:
            profile = ConfigurationRepository(session).backup_profiles.get_by_id(profile_id)
            if profile is None:
                raise ProfileNotFoundError(f"No backup profile with id={profile_id!r}.")
            profile.name = validated_name
            profile.enabled = enabled
            profile.interval_hours = interval_hours
            profile.retention_count = retention_count
            profile.location_type = location_type
            profile.encrypt_backups = encrypt_backups
            session.flush()
            return profile

    def delete_backup_profile(self, profile_id: int) -> None:
        """Soft-delete a backup profile.

        Raises:
            ProfileNotFoundError: No backup profile exists with that id.
        """
        with self._session_scope() as session:
            repo = ConfigurationRepository(session).backup_profiles
            profile = repo.get_by_id(profile_id)
            if profile is None:
                raise ProfileNotFoundError(f"No backup profile with id={profile_id!r}.")
            repo.delete(profile)

    def list_backup_profiles(self) -> list[BackupProfile]:
        """List every backup profile, ordered by id."""
        with self._session_scope() as session:
            return ConfigurationRepository(session).backup_profiles.list_all()

    def get_backup_profile(self, profile_id: int) -> BackupProfile | None:
        """Fetch a single backup profile by id, or ``None`` if not found."""
        with self._session_scope() as session:
            return ConfigurationRepository(session).backup_profiles.get_by_id(profile_id)

    # -- Remote configuration bundles --------------------------------------

    def create_configuration(
        self,
        *,
        name: str,
        theme_profile_id: int,
        print_profile_id: int,
        attendance_policy_profile_id: int,
        device_profile_id: int,
        backup_profile_id: int,
        description: str | None = None,
    ) -> RemoteConfiguration:
        """Create a new configuration bundle from five existing profiles.

        Raises:
            ConfigurationValidationError: ``name`` fails validation.
            ProfileNotFoundError: Any referenced profile id does not
                exist.
        """
        validated_name = _validate_name(name)
        with self._session_scope() as session:
            repo = ConfigurationRepository(session)
            profiles = self._fetch_required_profiles(
                repo,
                theme_profile_id=theme_profile_id,
                print_profile_id=print_profile_id,
                attendance_policy_profile_id=attendance_policy_profile_id,
                device_profile_id=device_profile_id,
                backup_profile_id=backup_profile_id,
            )
            configuration = RemoteConfiguration(
                name=validated_name,
                description=description,
                theme_profile=profiles.theme_profile,
                print_profile=profiles.print_profile,
                attendance_policy_profile=profiles.attendance_policy_profile,
                device_profile=profiles.device_profile,
                backup_profile=profiles.backup_profile,
            )
            return repo.add_configuration(configuration)

    def update_configuration(
        self,
        configuration_id: int,
        *,
        name: str,
        theme_profile_id: int,
        print_profile_id: int,
        attendance_policy_profile_id: int,
        device_profile_id: int,
        backup_profile_id: int,
        description: str | None = None,
    ) -> RemoteConfiguration:
        """Update an existing configuration bundle, bumping its version.

        Raises:
            ConfigurationValidationError: ``name`` fails validation.
            ConfigurationNotFoundError: No bundle exists with that id.
            ProfileNotFoundError: Any referenced profile id does not
                exist.
        """
        validated_name = _validate_name(name)
        with self._session_scope() as session:
            repo = ConfigurationRepository(session)
            configuration = repo.get_configuration(configuration_id)
            if configuration is None:
                raise ConfigurationNotFoundError(f"No configuration with id={configuration_id!r}.")
            profiles = self._fetch_required_profiles(
                repo,
                theme_profile_id=theme_profile_id,
                print_profile_id=print_profile_id,
                attendance_policy_profile_id=attendance_policy_profile_id,
                device_profile_id=device_profile_id,
                backup_profile_id=backup_profile_id,
            )
            configuration.name = validated_name
            configuration.description = description
            configuration.theme_profile = profiles.theme_profile
            configuration.print_profile = profiles.print_profile
            configuration.attendance_policy_profile = profiles.attendance_policy_profile
            configuration.device_profile = profiles.device_profile
            configuration.backup_profile = profiles.backup_profile
            configuration.version += 1
            session.flush()
            return configuration

    def delete_configuration(self, configuration_id: int) -> None:
        """Soft-delete a configuration bundle.

        Raises:
            ConfigurationNotFoundError: No bundle exists with that id.
        """
        with self._session_scope() as session:
            repo = ConfigurationRepository(session)
            configuration = repo.get_configuration(configuration_id)
            if configuration is None:
                raise ConfigurationNotFoundError(f"No configuration with id={configuration_id!r}.")
            repo.delete_configuration(configuration)

    def list_configurations(self) -> list[RemoteConfiguration]:
        """List every configuration bundle, ordered by name, with every profile loaded."""
        with self._session_scope() as session:
            return ConfigurationRepository(session).list_configurations()

    def get_configuration(self, configuration_id: int) -> RemoteConfiguration | None:
        """Fetch a single configuration bundle by id, with every profile loaded."""
        with self._session_scope() as session:
            return ConfigurationRepository(session).get_configuration(configuration_id)

    def _fetch_required_profiles(
        self,
        repo: ConfigurationRepository,
        *,
        theme_profile_id: int,
        print_profile_id: int,
        attendance_policy_profile_id: int,
        device_profile_id: int,
        backup_profile_id: int,
    ) -> _RequiredProfiles:
        """Fetch all five referenced profiles, raising on the first one that does not exist.

        Returns the loaded objects (rather than just confirming their
        ids exist) so the caller can assign them directly to
        :class:`~developer_suite.models.remote_configuration.RemoteConfiguration`'s
        relationship attributes — assigning only the ``*_id`` columns
        would leave a stale/unloaded ``.theme_profile`` etc. on the
        object this method's caller returns, which raises
        ``DetachedInstanceError`` once the session that created it has
        closed.

        Raises:
            ProfileNotFoundError: Any referenced profile id does not
                exist.
        """
        checks = (
            ("theme", repo.theme_profiles, theme_profile_id),
            ("print", repo.print_profiles, print_profile_id),
            ("attendance policy", repo.attendance_policy_profiles, attendance_policy_profile_id),
            ("device", repo.device_profiles, device_profile_id),
            ("backup", repo.backup_profiles, backup_profile_id),
        )
        loaded = []
        for label, sub_repo, profile_id in checks:
            profile = sub_repo.get_by_id(profile_id)
            if profile is None:
                raise ProfileNotFoundError(f"No {label} profile with id={profile_id!r}.")
            loaded.append(profile)
        return _RequiredProfiles(*loaded)

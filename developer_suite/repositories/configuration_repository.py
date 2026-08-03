"""Data access for the Remote Configuration foundation (Phase 5).

A single repository for all six models in this feature area, rather
than six near-identical repository classes: every profile type needs
only the generic CRUD :class:`~developer_suite.repositories.base_repository.BaseRepository`
already provides, so each is exposed as a plain, undecorated
``BaseRepository`` instance instead of a redundant subclass. Only
:class:`~developer_suite.models.remote_configuration.RemoteConfiguration`
needs a custom query (eager-loading its five bundled profiles), which
lives directly on this class.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from developer_suite.models.attendance_policy_profile import AttendancePolicyProfile
from developer_suite.models.backup_profile import BackupProfile
from developer_suite.models.device_profile import DeviceProfile
from developer_suite.models.print_profile import PrintProfile
from developer_suite.models.remote_configuration import RemoteConfiguration
from developer_suite.models.theme_profile import ThemeProfile
from developer_suite.repositories.base_repository import BaseRepository

_BUNDLE_RELATIONSHIPS = (
    RemoteConfiguration.theme_profile,
    RemoteConfiguration.print_profile,
    RemoteConfiguration.attendance_policy_profile,
    RemoteConfiguration.device_profile,
    RemoteConfiguration.backup_profile,
)


class ConfigurationRepository:
    """Data access for every Remote Configuration model, bound to one session.

    Attributes:
        theme_profiles: CRUD for :class:`~developer_suite.models.theme_profile.ThemeProfile`.
        print_profiles: CRUD for :class:`~developer_suite.models.print_profile.PrintProfile`.
        attendance_policy_profiles: CRUD for
            :class:`~developer_suite.models.attendance_policy_profile.AttendancePolicyProfile`.
        device_profiles: CRUD for :class:`~developer_suite.models.device_profile.DeviceProfile`.
        backup_profiles: CRUD for :class:`~developer_suite.models.backup_profile.BackupProfile`.
    """

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        self.session = session
        self.theme_profiles = BaseRepository[ThemeProfile](session, model=ThemeProfile)
        self.print_profiles = BaseRepository[PrintProfile](session, model=PrintProfile)
        self.attendance_policy_profiles = BaseRepository[AttendancePolicyProfile](
            session, model=AttendancePolicyProfile
        )
        self.device_profiles = BaseRepository[DeviceProfile](session, model=DeviceProfile)
        self.backup_profiles = BaseRepository[BackupProfile](session, model=BackupProfile)

    def _bundle_statement(self):
        """A ``select(RemoteConfiguration)`` with every profile relationship eagerly loaded."""
        statement = select(RemoteConfiguration)
        for relationship_attr in _BUNDLE_RELATIONSHIPS:
            statement = statement.options(joinedload(relationship_attr))
        return statement

    def get_configuration(self, configuration_id: int) -> RemoteConfiguration | None:
        """Fetch a single configuration bundle, with every profile eagerly loaded.

        Args:
            configuration_id: The bundle's ``id``.

        Returns:
            The matching bundle, or ``None`` if not found (or
            soft-deleted).
        """
        statement = self._bundle_statement().where(
            RemoteConfiguration.id == configuration_id, RemoteConfiguration.is_deleted.is_(False)
        )
        return self.session.execute(statement).unique().scalar_one_or_none()

    def list_configurations(self) -> list[RemoteConfiguration]:
        """List every configuration bundle, with every profile eagerly loaded, by name.

        Returns:
            Every non-deleted bundle, ordered by name.
        """
        statement = (
            self._bundle_statement()
            .where(RemoteConfiguration.is_deleted.is_(False))
            .order_by(RemoteConfiguration.name)
        )
        return list(self.session.execute(statement).unique().scalars().all())

    def add_configuration(self, configuration: RemoteConfiguration) -> RemoteConfiguration:
        """Stage a new configuration bundle for insertion and flush it."""
        self.session.add(configuration)
        self.session.flush()
        return configuration

    def delete_configuration(self, configuration: RemoteConfiguration) -> None:
        """Soft-delete a configuration bundle."""
        configuration.soft_delete()
        self.session.flush()

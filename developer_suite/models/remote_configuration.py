"""Remote configuration ORM model — a named bundle of profile templates.

A :class:`RemoteConfiguration` composes one of each profile type into a
single, named, reusable template (e.g. ``"الحزمة الافتراضية"``,
``"باقة رمضان"``). This phase only defines how such bundles are stored
and edited inside the Developer Suite — nothing here is deployed,
synchronized, or communicated to any customer application (see this
package's ``__init__.py`` and
``developer_suite/modules/remote_configuration.py``).
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from developer_suite.database.base import DeveloperSuiteBaseModel
from developer_suite.models.attendance_policy_profile import AttendancePolicyProfile
from developer_suite.models.backup_profile import BackupProfile
from developer_suite.models.device_profile import DeviceProfile
from developer_suite.models.print_profile import PrintProfile
from developer_suite.models.theme_profile import ThemeProfile


class RemoteConfiguration(DeveloperSuiteBaseModel):
    """A named bundle of one theme, print, attendance-policy, device, and backup profile.

    Attributes:
        name: A unique, human-readable identifier for this bundle.
        description: Optional free-form notes about when/why to use
            this bundle.
        version: A simple, monotonically-meaningful integer a later
            (not-yet-approved) synchronization phase can use to detect
            that a bundle changed; unused by anything in this phase.
        theme_profile_id: The bundled :class:`~developer_suite.models.theme_profile.ThemeProfile`.
        print_profile_id: The bundled :class:`~developer_suite.models.print_profile.PrintProfile`.
        attendance_policy_profile_id: The bundled
            :class:`~developer_suite.models.attendance_policy_profile.AttendancePolicyProfile`.
        device_profile_id: The bundled :class:`~developer_suite.models.device_profile.DeviceProfile`.
        backup_profile_id: The bundled :class:`~developer_suite.models.backup_profile.BackupProfile`.
    """

    name: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(default=1, nullable=False)

    theme_profile_id: Mapped[int] = mapped_column(
        ForeignKey("theme_profiles.id"), nullable=False, index=True
    )
    print_profile_id: Mapped[int] = mapped_column(
        ForeignKey("print_profiles.id"), nullable=False, index=True
    )
    attendance_policy_profile_id: Mapped[int] = mapped_column(
        ForeignKey("attendance_policy_profiles.id"), nullable=False, index=True
    )
    device_profile_id: Mapped[int] = mapped_column(
        ForeignKey("device_profiles.id"), nullable=False, index=True
    )
    backup_profile_id: Mapped[int] = mapped_column(
        ForeignKey("backup_profiles.id"), nullable=False, index=True
    )

    theme_profile: Mapped["ThemeProfile"] = relationship("ThemeProfile")
    print_profile: Mapped["PrintProfile"] = relationship("PrintProfile")
    attendance_policy_profile: Mapped["AttendancePolicyProfile"] = relationship(
        "AttendancePolicyProfile"
    )
    device_profile: Mapped["DeviceProfile"] = relationship("DeviceProfile")
    backup_profile: Mapped["BackupProfile"] = relationship("BackupProfile")

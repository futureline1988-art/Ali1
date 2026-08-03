"""Backup profile ORM model — a reusable backup-policy template."""

from __future__ import annotations

from enum import Enum

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from developer_suite.database.base import DeveloperSuiteBaseModel
from models.base import enum_column_type


class BackupLocationType(str, Enum):
    """Where a backup is ultimately stored."""

    LOCAL = "local"
    CLOUD = "cloud"


class BackupProfile(DeveloperSuiteBaseModel):
    """A reusable backup-policy template.

    Attributes:
        name: A unique, human-readable identifier for this profile.
        enabled: Whether scheduled automatic backups are on.
        interval_hours: Hours between automatic backups, matching
            :attr:`~models.company_settings.CompanySettings.backup_interval_hours`'s
            default.
        retention_count: Number of past backups to keep, matching
            :attr:`~models.company_settings.CompanySettings.backup_retention_count`'s
            default.
        location_type: Where the backup is ultimately stored (see
            :class:`BackupLocationType`).
        encrypt_backups: Whether backups are encrypted at rest,
            matching the Attendance Client's own
            ``services/backup_service.py`` behavior, which always
            encrypts.
    """

    name: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    interval_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    retention_count: Mapped[int] = mapped_column(Integer, default=14, nullable=False)
    location_type: Mapped[BackupLocationType] = mapped_column(
        enum_column_type(BackupLocationType), default=BackupLocationType.LOCAL, nullable=False
    )
    encrypt_backups: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

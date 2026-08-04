"""Data access for the Attendance Server's software-update domain.

Five small repositories, one per table in :mod:`server.models.update`
(:class:`UpdateAuditEvent` is written through
:class:`UpdateVersionRepository` since every audit event is always
created alongside a version mutation in the same transaction — no
separate repository needed for a table nothing ever queries back
except for a version's own history, exposed via
:meth:`UpdateVersionRepository.list_audit_events`).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.models.update import (
    DeviceUpdateStatus,
    DeviceUpdateStatusValue,
    PublishStatus,
    TargetScope,
    UpdateAuditEvent,
    UpdatePackage,
    UpdateRollback,
    UpdateTarget,
    UpdateVersion,
)
from server.repositories.base_repository import BaseRepository


class UpdateVersionRepository(BaseRepository[UpdateVersion]):
    """Data access for :class:`~server.models.update.UpdateVersion`."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        super().__init__(session, model=UpdateVersion)

    def get_by_version_string(self, version: str) -> UpdateVersion | None:
        """Fetch a single version row by its exact version string."""
        statement = select(UpdateVersion).where(
            UpdateVersion.version == version, UpdateVersion.is_deleted.is_(False)
        )
        return self.session.execute(statement).scalar_one_or_none()

    def list_by_status(self, *statuses: PublishStatus) -> list[UpdateVersion]:
        """List every version currently in one of ``statuses``, newest-created first."""
        statement = (
            select(UpdateVersion)
            .where(UpdateVersion.publish_status.in_(statuses), UpdateVersion.is_deleted.is_(False))
            .order_by(UpdateVersion.id.desc())
        )
        return list(self.session.execute(statement).scalars().all())

    def add_audit_event(
        self, *, update_version_id: int, action: str, performed_by: str, description: str | None = None
    ) -> UpdateAuditEvent:
        """Append one audit event for ``update_version_id``, in the caller's own transaction."""
        event = UpdateAuditEvent(
            update_version_id=update_version_id,
            action=action,
            performed_by=performed_by,
            description=description,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def list_audit_events(self, update_version_id: int) -> list[UpdateAuditEvent]:
        """List every audit event for one version, oldest first."""
        statement = (
            select(UpdateAuditEvent)
            .where(UpdateAuditEvent.update_version_id == update_version_id)
            .order_by(UpdateAuditEvent.id)
        )
        return list(self.session.execute(statement).scalars().all())


class UpdatePackageRepository(BaseRepository[UpdatePackage]):
    """Data access for :class:`~server.models.update.UpdatePackage`."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        super().__init__(session, model=UpdatePackage)

    def list_for_version(self, update_version_id: int) -> list[UpdatePackage]:
        """List every package (setup and/or portable) for one version."""
        statement = select(UpdatePackage).where(
            UpdatePackage.update_version_id == update_version_id, UpdatePackage.is_deleted.is_(False)
        )
        return list(self.session.execute(statement).scalars().all())

    def get_for_version_and_type(self, update_version_id: int, package_type) -> UpdatePackage | None:
        """Fetch one version's package of a specific type, if uploaded."""
        statement = select(UpdatePackage).where(
            UpdatePackage.update_version_id == update_version_id,
            UpdatePackage.package_type == package_type,
            UpdatePackage.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()


class UpdateTargetRepository(BaseRepository[UpdateTarget]):
    """Data access for :class:`~server.models.update.UpdateTarget`."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        super().__init__(session, model=UpdateTarget)

    def list_for_version(self, update_version_id: int) -> list[UpdateTarget]:
        """List every target row for one version."""
        statement = select(UpdateTarget).where(
            UpdateTarget.update_version_id == update_version_id, UpdateTarget.is_deleted.is_(False)
        )
        return list(self.session.execute(statement).scalars().all())

    def replace_targets(self, update_version_id: int, targets: list[UpdateTarget]) -> None:
        """Hard-delete every existing target row for a version and insert ``targets`` instead.

        Targeting is a *current selection*, not a history — unlike
        :class:`~server.models.update.UpdateRollback`/
        :class:`~server.models.update.UpdateAuditEvent`, there is no
        value in keeping a superseded targeting choice around, so this
        is a real delete rather than a soft one.
        """
        existing = self.session.execute(
            select(UpdateTarget).where(UpdateTarget.update_version_id == update_version_id)
        ).scalars().all()
        for row in existing:
            self.session.delete(row)
        self.session.flush()
        for target in targets:
            self.session.add(target)
        self.session.flush()

    def is_targeted(self, update_version_id: int, device_public_id: str) -> bool:
        """Whether ``device_public_id`` is covered by this version's current targeting."""
        statement = select(UpdateTarget).where(
            UpdateTarget.update_version_id == update_version_id,
            UpdateTarget.is_deleted.is_(False),
            (UpdateTarget.scope == TargetScope.ALL)
            | (
                (UpdateTarget.scope == TargetScope.DEVICE)
                & (UpdateTarget.target_device_public_id == device_public_id)
            ),
        )
        return self.session.execute(statement).scalars().first() is not None


class UpdateRollbackRepository(BaseRepository[UpdateRollback]):
    """Data access for :class:`~server.models.update.UpdateRollback`."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        super().__init__(session, model=UpdateRollback)

    def list_for_version(self, update_version_id: int) -> list[UpdateRollback]:
        """List every rollback ever recorded against one version, oldest first."""
        statement = (
            select(UpdateRollback)
            .where(UpdateRollback.update_version_id == update_version_id)
            .order_by(UpdateRollback.id)
        )
        return list(self.session.execute(statement).scalars().all())


class DeviceUpdateStatusRepository(BaseRepository[DeviceUpdateStatus]):
    """Data access for :class:`~server.models.update.DeviceUpdateStatus`."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        super().__init__(session, model=DeviceUpdateStatus)

    def get(self, device_public_id: str, update_version_id: int) -> DeviceUpdateStatus | None:
        """Fetch one device's status row for one version, if it has ever reported one."""
        statement = select(DeviceUpdateStatus).where(
            DeviceUpdateStatus.device_public_id == device_public_id,
            DeviceUpdateStatus.update_version_id == update_version_id,
        )
        return self.session.execute(statement).scalar_one_or_none()

    def upsert(
        self,
        *,
        device_public_id: str,
        update_version_id: int,
        status: DeviceUpdateStatusValue,
        progress_percent: int,
        error_message: str | None,
        reported_at: datetime,
    ) -> DeviceUpdateStatus:
        """Create or overwrite one device's status row for one version."""
        row = self.get(device_public_id, update_version_id)
        if row is None:
            row = DeviceUpdateStatus(
                device_public_id=device_public_id,
                update_version_id=update_version_id,
                status=status,
                progress_percent=progress_percent,
                error_message=error_message,
                reported_at=reported_at,
            )
            self.session.add(row)
        else:
            row.status = status
            row.progress_percent = progress_percent
            row.error_message = error_message
            row.reported_at = reported_at
        self.session.flush()
        return row

    def list_for_version(self, update_version_id: int) -> list[DeviceUpdateStatus]:
        """List every device's status row for one version."""
        statement = select(DeviceUpdateStatus).where(
            DeviceUpdateStatus.update_version_id == update_version_id
        )
        return list(self.session.execute(statement).scalars().all())

    def list_all(self) -> list[DeviceUpdateStatus]:
        """List every device status row across every version (for dashboard aggregation)."""
        return list(self.session.execute(select(DeviceUpdateStatus)).scalars().all())

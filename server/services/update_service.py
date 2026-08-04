"""Software update distribution business logic.

Owns every mutation and query against :mod:`server.models.update`.
Deliberately never touches package *contents* beyond re-verifying a
checksum on upload (see :meth:`UpdateService.add_package`) — signing
and signature verification are entirely a Developer-Suite/Attendance
-Client concern (see :mod:`server.models.update`'s module docstring),
the same "server stores, never decides" boundary this platform already
draws around license verification (``licensing/keys.py``'s embedded
public key is read by the Attendance Client, never by this server).

A version's :attr:`~server.models.update.UpdateVersion.publish_status`
records the administrator's *stated intent* (draft/scheduled/published/
disabled/rolled back); whether a version is actually live *right now*
is instead computed at query time by :func:`_is_effectively_live`
(``PUBLISHED``, or ``SCHEDULED`` with a due ``scheduled_at``) — no
background job ever flips a row from ``SCHEDULED`` to ``PUBLISHED``,
which would otherwise need its own scheduler and its own race-condition
handling for zero actual benefit over a plain time comparison at read
time.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from database.database import Database
from server.config import ServerConfig
from server.models.update import (
    DeviceUpdateStatus,
    DeviceUpdateStatusValue,
    PackageType,
    PublishStatus,
    TargetScope,
    UpdatePackage,
    UpdateRollback,
    UpdateTarget,
    UpdateVersion,
)
from server.repositories.update_repository import (
    DeviceUpdateStatusRepository,
    UpdatePackageRepository,
    UpdateRollbackRepository,
    UpdateTargetRepository,
    UpdateVersionRepository,
)
from server.services.base_service import BaseService


class UpdateServiceError(Exception):
    """Base class for update-management failures the API layer should translate."""


class UpdateVersionNotFoundError(UpdateServiceError):
    """No :class:`~server.models.update.UpdateVersion` exists with the given id."""


class DuplicateVersionError(UpdateServiceError):
    """An :class:`~server.models.update.UpdateVersion` with this version string already exists."""


class ChecksumMismatchError(UpdateServiceError):
    """The uploaded package's actual checksum does not match the claimed one."""


class NoPackageUploadedError(UpdateServiceError):
    """A version cannot be published before at least one package has been uploaded."""


def _version_key(version: str) -> tuple[int, ...]:
    """Parse a dotted numeric version string into a comparable tuple.

    ``"1.9.0"`` sorts before ``"1.10.0"`` (unlike a plain string
    comparison, which would not). Non-numeric segments compare as
    ``0`` rather than raising, so a malformed version string never
    crashes a listing — it simply sorts low.
    """
    return tuple(int(part) if part.isdigit() else 0 for part in re.split(r"[.\-]", version))


def _is_effectively_live(version: UpdateVersion, *, now: datetime) -> bool:
    """Whether ``version`` is actually available to clients right now.

    ``PUBLISHED`` always is; ``SCHEDULED`` becomes live the moment its
    ``scheduled_at`` is reached, computed here rather than by any
    background job (see this module's docstring).
    """
    if version.publish_status is PublishStatus.PUBLISHED:
        return True
    if version.publish_status is PublishStatus.SCHEDULED:
        return version.scheduled_at is not None and version.scheduled_at <= now
    return False


@dataclass(frozen=True)
class UpdateDashboardStats:
    """Aggregate update-distribution statistics for the Developer Dashboard.

    Attributes:
        latest_deployed_version: The highest version string with at
            least one device reporting
            :attr:`~server.models.update.DeviceUpdateStatusValue.INSTALLED`;
            ``None`` if no device has ever reported a successful
            install.
        companies_per_version: ``{version: installed_device_count}``
            for every version with at least one installed device.
        pending_count: Devices currently anywhere between "pending"
            and "verified" (not yet installed, not yet failed).
        failed_count: Devices whose most recent report for any version
            is :attr:`~server.models.update.DeviceUpdateStatusValue.FAILED`.
        successful_count: Devices whose most recent report for any
            version is :attr:`~server.models.update.DeviceUpdateStatusValue.INSTALLED`.
        average_download_progress_percent: Mean
            :attr:`~server.models.update.DeviceUpdateStatus.progress_percent`
            among devices currently
            :attr:`~server.models.update.DeviceUpdateStatusValue.DOWNLOADING`;
            ``None`` if none are.
    """

    latest_deployed_version: str | None
    companies_per_version: dict[str, int]
    pending_count: int
    failed_count: int
    successful_count: int
    average_download_progress_percent: float | None


class UpdateService(BaseService):
    """Create, publish, target, roll back, and report on software updates."""

    def __init__(self, database: Database, *, config: ServerConfig) -> None:
        """Create an update service bound to ``database`` and this server's configuration.

        Args:
            database: This server's own database.
            config: This server's configuration; supplies
                :attr:`~server.config.ServerConfig.paths` for where
                uploaded package files are stored on disk.
        """
        super().__init__(database)
        self._config = config

    # -- Version lifecycle --------------------------------------------------

    def create_version(
        self,
        *,
        version: str,
        release_notes: str | None,
        min_supported_version: str | None,
        update_type,
        created_by: str,
    ) -> UpdateVersion:
        """Create a new, draft version record.

        Raises:
            DuplicateVersionError: A version with this exact string
                already exists.
        """
        with self._session_scope() as session:
            repo = UpdateVersionRepository(session)
            if repo.get_by_version_string(version) is not None:
                raise DuplicateVersionError(f"Version {version!r} already exists.")
            row = UpdateVersion(
                version=version,
                release_notes=release_notes,
                min_supported_version=min_supported_version,
                update_type=update_type,
                publish_status=PublishStatus.DRAFT,
                created_by=created_by,
            )
            repo.add(row)
            repo.add_audit_event(update_version_id=row.id, action="created", performed_by=created_by)
            return row

    def add_package(
        self,
        update_version_id: int,
        *,
        package_type: PackageType,
        file_bytes: bytes,
        claimed_checksum_sha256: str,
        signature_base64: str,
        original_filename: str,
        performed_by: str,
    ) -> UpdatePackage:
        """Store an uploaded package file, verifying its checksum on receipt.

        Args:
            update_version_id: Which version this package belongs to.
            package_type: Setup installer or portable archive.
            file_bytes: The raw package file contents.
            claimed_checksum_sha256: The SHA-256 the Developer Suite
                computed before upload; re-verified here against
                ``file_bytes`` rather than trusted outright.
            signature_base64: The Ed25519 signature the Developer
                Suite already produced with its own signing private
                key — stored verbatim; this server never verifies it
                (see this module's docstring).
            original_filename: Used only to preserve the file's
                extension on disk.
            performed_by: The administrator performing this upload.

        Returns:
            The stored package row.

        Raises:
            UpdateVersionNotFoundError: No such version.
            ChecksumMismatchError: ``claimed_checksum_sha256`` does not
                match the actual SHA-256 of ``file_bytes``.
        """
        actual_checksum = hashlib.sha256(file_bytes).hexdigest()
        if actual_checksum != claimed_checksum_sha256:
            raise ChecksumMismatchError(
                f"Claimed checksum {claimed_checksum_sha256!r} does not match "
                f"the actual checksum {actual_checksum!r}."
            )

        with self._session_scope() as session:
            version_repo = UpdateVersionRepository(session)
            version = version_repo.get_by_id(update_version_id)
            if version is None:
                raise UpdateVersionNotFoundError(f"No update version with id={update_version_id!r}.")

            suffix = Path(original_filename).suffix or ".bin"
            relative_path = Path("update_packages") / f"{version.version}_{package_type.value}{suffix}"
            absolute_path = self._config.paths.data_dir / relative_path
            absolute_path.parent.mkdir(parents=True, exist_ok=True)
            absolute_path.write_bytes(file_bytes)

            package_repo = UpdatePackageRepository(session)
            existing = package_repo.get_for_version_and_type(update_version_id, package_type)
            if existing is None:
                package = UpdatePackage(
                    update_version_id=update_version_id,
                    package_type=package_type,
                    file_path=str(relative_path),
                    checksum_sha256=actual_checksum,
                    signature_base64=signature_base64,
                    size_bytes=len(file_bytes),
                )
                package_repo.add(package)
            else:
                existing.file_path = str(relative_path)
                existing.checksum_sha256 = actual_checksum
                existing.signature_base64 = signature_base64
                existing.size_bytes = len(file_bytes)
                session.flush()
                package = existing

            version_repo.add_audit_event(
                update_version_id=update_version_id,
                action="package_uploaded",
                performed_by=performed_by,
                description=f"{package_type.value} package ({len(file_bytes)} bytes).",
            )
            return package

    def set_targets(
        self,
        update_version_id: int,
        *,
        scope: TargetScope,
        device_public_ids: list[str] | None,
        performed_by: str,
    ) -> list[UpdateTarget]:
        """Replace a version's current targeting with a new selection.

        Args:
            update_version_id: The version to target.
            scope: :attr:`~server.models.update.TargetScope.ALL` to
                target every device, or
                :attr:`~server.models.update.TargetScope.DEVICE` to
                target only ``device_public_ids``.
            device_public_ids: Required and non-empty when ``scope``
                is :attr:`~server.models.update.TargetScope.DEVICE`;
                ignored otherwise. The Developer Suite resolves
                "specific customers"/"customer groups" down to this
                flat list of device public ids before calling this
                method — this server has no concept of either (see
                :mod:`server.models.update`'s module docstring).
            performed_by: The administrator setting this targeting.

        Raises:
            UpdateVersionNotFoundError: No such version.
        """
        with self._session_scope() as session:
            version_repo = UpdateVersionRepository(session)
            if version_repo.get_by_id(update_version_id) is None:
                raise UpdateVersionNotFoundError(f"No update version with id={update_version_id!r}.")

            if scope is TargetScope.ALL:
                rows = [UpdateTarget(update_version_id=update_version_id, scope=TargetScope.ALL)]
            else:
                unique_ids = sorted(set(device_public_ids or []))
                rows = [
                    UpdateTarget(
                        update_version_id=update_version_id,
                        scope=TargetScope.DEVICE,
                        target_device_public_id=device_id,
                    )
                    for device_id in unique_ids
                ]

            UpdateTargetRepository(session).replace_targets(update_version_id, rows)
            version_repo.add_audit_event(
                update_version_id=update_version_id,
                action="targets_set",
                performed_by=performed_by,
                description=f"scope={scope.value}, devices={len(rows) if scope is TargetScope.DEVICE else 'all'}",
            )
            return rows

    def publish(self, update_version_id: int, *, performed_by: str) -> UpdateVersion:
        """Publish a version immediately.

        Raises:
            UpdateVersionNotFoundError: No such version.
            NoPackageUploadedError: No package has been uploaded yet.
        """
        with self._session_scope() as session:
            version_repo = UpdateVersionRepository(session)
            version = version_repo.get_by_id(update_version_id)
            if version is None:
                raise UpdateVersionNotFoundError(f"No update version with id={update_version_id!r}.")
            if not UpdatePackageRepository(session).list_for_version(update_version_id):
                raise NoPackageUploadedError(
                    "Cannot publish a version with no uploaded package."
                )
            version.publish_status = PublishStatus.PUBLISHED
            version.published_at = datetime.now(timezone.utc)
            session.flush()
            version_repo.add_audit_event(
                update_version_id=update_version_id, action="published", performed_by=performed_by
            )
            return version

    def schedule(self, update_version_id: int, *, scheduled_at: datetime, performed_by: str) -> UpdateVersion:
        """Schedule a version to become live at ``scheduled_at``.

        Raises:
            UpdateVersionNotFoundError: No such version.
            NoPackageUploadedError: No package has been uploaded yet.
        """
        with self._session_scope() as session:
            version_repo = UpdateVersionRepository(session)
            version = version_repo.get_by_id(update_version_id)
            if version is None:
                raise UpdateVersionNotFoundError(f"No update version with id={update_version_id!r}.")
            if not UpdatePackageRepository(session).list_for_version(update_version_id):
                raise NoPackageUploadedError(
                    "Cannot schedule a version with no uploaded package."
                )
            version.publish_status = PublishStatus.SCHEDULED
            version.scheduled_at = scheduled_at
            session.flush()
            version_repo.add_audit_event(
                update_version_id=update_version_id,
                action="scheduled",
                performed_by=performed_by,
                description=f"scheduled_at={scheduled_at.isoformat()}",
            )
            return version

    def disable(self, update_version_id: int, *, performed_by: str) -> UpdateVersion:
        """Disable a version, immediately removing it from every latest/assigned query.

        Raises:
            UpdateVersionNotFoundError: No such version.
        """
        with self._session_scope() as session:
            version_repo = UpdateVersionRepository(session)
            version = version_repo.get_by_id(update_version_id)
            if version is None:
                raise UpdateVersionNotFoundError(f"No update version with id={update_version_id!r}.")
            version.publish_status = PublishStatus.DISABLED
            session.flush()
            version_repo.add_audit_event(
                update_version_id=update_version_id, action="disabled", performed_by=performed_by
            )
            return version

    def rollback(self, update_version_id: int, *, performed_by: str, reason: str | None = None) -> UpdateVersion:
        """Roll back a version: excludes it from future latest/assigned queries.

        Never deletes the version or any of its history — see
        :class:`~server.models.update.UpdateRollback`'s own docstring.

        Raises:
            UpdateVersionNotFoundError: No such version.
        """
        with self._session_scope() as session:
            version_repo = UpdateVersionRepository(session)
            version = version_repo.get_by_id(update_version_id)
            if version is None:
                raise UpdateVersionNotFoundError(f"No update version with id={update_version_id!r}.")
            version.publish_status = PublishStatus.ROLLED_BACK
            session.flush()
            UpdateRollbackRepository(session).add(
                UpdateRollback(update_version_id=update_version_id, rolled_back_by=performed_by, reason=reason)
            )
            version_repo.add_audit_event(
                update_version_id=update_version_id,
                action="rolled_back",
                performed_by=performed_by,
                description=reason,
            )
            return version

    # -- Client-facing reads --------------------------------------------------

    def get_latest_global(self, *, now: datetime | None = None) -> UpdateVersion | None:
        """The highest-version currently-live update, ignoring targeting entirely."""
        reference = now or datetime.now(timezone.utc)
        with self._session_scope() as session:
            candidates = UpdateVersionRepository(session).list_by_status(
                PublishStatus.PUBLISHED, PublishStatus.SCHEDULED
            )
            live = [v for v in candidates if _is_effectively_live(v, now=reference)]
            if not live:
                return None
            return max(live, key=lambda v: _version_key(v.version))

    def get_assigned_for_device(
        self, device_public_id: str, *, now: datetime | None = None
    ) -> UpdateVersion | None:
        """The highest-version currently-live update actually targeted at ``device_public_id``."""
        reference = now or datetime.now(timezone.utc)
        with self._session_scope() as session:
            version_repo = UpdateVersionRepository(session)
            target_repo = UpdateTargetRepository(session)
            candidates = version_repo.list_by_status(PublishStatus.PUBLISHED, PublishStatus.SCHEDULED)
            live = [
                v
                for v in candidates
                if _is_effectively_live(v, now=reference) and target_repo.is_targeted(v.id, device_public_id)
            ]
            if not live:
                return None
            return max(live, key=lambda v: _version_key(v.version))

    def list_history(self, *, limit: int = 50) -> list[UpdateVersion]:
        """Every non-draft version, most recently created first."""
        with self._session_scope() as session:
            return UpdateVersionRepository(session).list_by_status(
                PublishStatus.PUBLISHED,
                PublishStatus.SCHEDULED,
                PublishStatus.DISABLED,
                PublishStatus.ROLLED_BACK,
            )[:limit]

    def get_version(self, update_version_id: int) -> UpdateVersion | None:
        """Fetch a single version by id, or ``None`` if not found."""
        with self._session_scope() as session:
            return UpdateVersionRepository(session).get_by_id(update_version_id)

    def list_versions(self) -> list[UpdateVersion]:
        """List every version regardless of status, newest first (Developer Suite history view)."""
        with self._session_scope() as session:
            statement_result = UpdateVersionRepository(session).list_all()
            return sorted(statement_result, key=lambda v: v.id, reverse=True)

    def get_packages(self, update_version_id: int) -> list[UpdatePackage]:
        """List every package uploaded for one version."""
        with self._session_scope() as session:
            return UpdatePackageRepository(session).list_for_version(update_version_id)

    def get_package(self, package_id: int) -> UpdatePackage | None:
        """Fetch a single package by id, or ``None`` if not found."""
        with self._session_scope() as session:
            return UpdatePackageRepository(session).get_by_id(package_id)

    def get_package_file_path(self, package: UpdatePackage) -> Path:
        """Resolve a package row's file to its absolute path on disk."""
        return self._config.paths.data_dir / package.file_path

    # -- Device status reporting --------------------------------------------

    def report_device_status(
        self,
        *,
        device_public_id: str,
        update_version_id: int,
        status: DeviceUpdateStatusValue,
        progress_percent: int,
        error_message: str | None,
    ) -> DeviceUpdateStatus:
        """Upsert one device's progress applying one version.

        Raises:
            UpdateVersionNotFoundError: No such version.
        """
        with self._session_scope() as session:
            if UpdateVersionRepository(session).get_by_id(update_version_id) is None:
                raise UpdateVersionNotFoundError(f"No update version with id={update_version_id!r}.")
            return DeviceUpdateStatusRepository(session).upsert(
                device_public_id=device_public_id,
                update_version_id=update_version_id,
                status=status,
                progress_percent=max(0, min(100, progress_percent)),
                error_message=error_message,
                reported_at=datetime.now(timezone.utc),
            )

    # -- Dashboard aggregation --------------------------------------------------

    def get_dashboard_stats(self) -> UpdateDashboardStats:
        """Aggregate device update statuses into Developer Dashboard statistics."""
        with self._session_scope() as session:
            version_repo = UpdateVersionRepository(session)
            statuses = DeviceUpdateStatusRepository(session).list_all()

            versions_by_id = {v.id: v for v in version_repo.list_all()}

            companies_per_version: dict[str, int] = {}
            pending = failed = successful = 0
            downloading_progress: list[int] = []

            for row in statuses:
                if row.status is DeviceUpdateStatusValue.INSTALLED:
                    successful += 1
                    version = versions_by_id.get(row.update_version_id)
                    if version is not None:
                        companies_per_version[version.version] = (
                            companies_per_version.get(version.version, 0) + 1
                        )
                elif row.status is DeviceUpdateStatusValue.FAILED:
                    failed += 1
                elif row.status is DeviceUpdateStatusValue.DOWNLOADING:
                    pending += 1
                    downloading_progress.append(row.progress_percent)
                else:
                    pending += 1

            latest_deployed_version = (
                max(companies_per_version, key=_version_key) if companies_per_version else None
            )
            average_progress = (
                sum(downloading_progress) / len(downloading_progress) if downloading_progress else None
            )

            return UpdateDashboardStats(
                latest_deployed_version=latest_deployed_version,
                companies_per_version=companies_per_version,
                pending_count=pending,
                failed_count=failed,
                successful_count=successful,
                average_download_progress_percent=average_progress,
            )

"""Software update distribution: version metadata, packages, targeting, and status.

Phase 14's server-side domain, following the same "no business
knowledge on the wire, addressing by device public id" shape Phase 13
established for configuration (see
:mod:`developer_suite.sync.configuration_sync`'s docstring) — but
delivered through dedicated REST endpoints
(:mod:`server.api.routers.updates`) rather than the generic sync
ledger, since update packages are large binary files, not small JSON
change payloads the ``ChangeRecord.payload`` column is designed for.

Six tables:

* :class:`UpdateVersion` — one row per released version. Never
  deleted; its :attr:`~UpdateVersion.publish_status` moves it through
  draft -> scheduled/published -> disabled/rolled back.
* :class:`UpdatePackage` — the actual downloadable artifact(s) for one
  version (setup and/or portable), with the checksum and signature a
  client must verify before ever running one.
* :class:`UpdateTarget` — who a version is offered to: every device
  (:attr:`TargetScope.ALL`), or one specific device
  (:attr:`TargetScope.DEVICE`) — the exact ``target_device_public_id``
  addressing convention
  :class:`~developer_suite.models.configuration_publication.ConfigurationPublication`
  already established, reused here for the same reason: this server
  has no concept of "customer" or "customer group" at all (see
  ``server/models/device.py``'s own docstring on why ``SyncDevice``
  carries no customer link) — the Developer Suite resolves a customer
  or customer-group selection down to concrete device public ids
  before ever calling this API.
* :class:`UpdateRollback` — an append-only record of every rollback
  action, alongside a version's own :attr:`~UpdateVersion.publish_status`
  moving to :attr:`PublishStatus.ROLLED_BACK` — nothing is ever
  deleted, mirroring
  :class:`~developer_suite.models.configuration_publication.ConfigurationPublication`'s
  own "rollback creates new history, never erases old" discipline.
* :class:`DeviceUpdateStatus` — one row per ``(device, version)`` pair,
  upserted by the Attendance Client as it checks, downloads, verifies,
  and installs — what the Developer Dashboard's update statistics
  (companies per version, pending/failed/successful, download
  progress) are computed from.
* :class:`UpdateAuditEvent` — an append-only log of every admin action
  taken against a version (create, package upload, publish, schedule,
  disable, rollback), each carrying ``performed_by``. Deliberately a
  new, update-scoped table rather than reusing
  :class:`~server.models.admin_audit_log.AdminAuditLog`, whose own
  docstring scopes it to authentication events specifically; recording
  ``performed_by`` on the action itself is the same "who did this"
  discipline
  :class:`~developer_suite.models.configuration_publication.ConfigurationPublication.published_by`
  already established for Phase 13's "only administrators can
  publish/every publish action must be audited" requirement.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from models.base import UTCDateTime, enum_column_type
from server.database.base import ServerBaseModel


class UpdateType(str, Enum):
    """How strongly a client should be pushed toward installing a version."""

    OPTIONAL = "optional"
    RECOMMENDED = "recommended"
    CRITICAL = "critical"
    MANDATORY = "mandatory"


class PackageType(str, Enum):
    """Which installer artifact a package row is."""

    SETUP = "setup"
    PORTABLE = "portable"


class PublishStatus(str, Enum):
    """An :class:`UpdateVersion`'s current lifecycle state."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    DISABLED = "disabled"
    ROLLED_BACK = "rolled_back"


class TargetScope(str, Enum):
    """Who an :class:`UpdateTarget` row applies to."""

    ALL = "all"
    DEVICE = "device"


class DeviceUpdateStatusValue(str, Enum):
    """One device's progress applying one specific version."""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    VERIFIED = "verified"
    INSTALLED = "installed"
    FAILED = "failed"
    POSTPONED = "postponed"


class UpdateVersion(ServerBaseModel):
    """One released (or in-progress) application version.

    Attributes:
        version: The version string (e.g. ``"1.1.0"``); unique.
        release_notes: Free-form release notes, shown to the customer.
        min_supported_version: The oldest client version still allowed
            to skip straight to this one without an intermediate
            update, if enforced; ``None`` means no restriction.
        update_type: How strongly to push this version (see
            :class:`UpdateType`).
        publish_status: This version's current lifecycle state (see
            :class:`PublishStatus`).
        scheduled_at: When :attr:`publish_status` should
            automatically become effective, if this version was
            scheduled rather than published immediately; ``None`` for
            an immediate publish or a still-draft version.
        published_at: When :attr:`publish_status` actually became
            :attr:`~PublishStatus.PUBLISHED`.
        created_by: The administrator who created this version record.
    """

    version: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    release_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    min_supported_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    update_type: Mapped[UpdateType] = mapped_column(
        enum_column_type(UpdateType), nullable=False, default=UpdateType.OPTIONAL
    )
    publish_status: Mapped[PublishStatus] = mapped_column(
        enum_column_type(PublishStatus), nullable=False, default=PublishStatus.DRAFT, index=True
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)


class UpdatePackage(ServerBaseModel):
    """One downloadable artifact (setup or portable) for one :class:`UpdateVersion`.

    Attributes:
        update_version_id: The version this package belongs to.
        package_type: Setup installer or portable archive (see
            :class:`PackageType`).
        file_path: Path to the stored file, relative to
            :attr:`~server.config.ServerPaths.data_dir` — never an
            absolute path, so a relocated data directory does not
            silently break every download.
        checksum_sha256: SHA-256 hex digest of the file, computed by
            the Developer Suite before upload and re-verified by this
            server on receipt — the same "recompute rather than trust
            the wire" discipline
            :meth:`~server.services.sync_service.SyncService.compute_checksum`
            already applies to configuration payloads.
        signature_base64: The Ed25519 signature of the file's raw
            bytes, base64-encoded, produced by the Developer Suite's
            own update-signing private key (see
            :mod:`licensing.crypto.signing` — the module Phase 1 built
            specifically for this kind of vendor-side signing). This
            server never holds that private key and cannot produce a
            signature itself; it only stores and serves what the
            Developer Suite already signed.
        size_bytes: The file's exact size, so a client can validate a
            completed download and support HTTP ``Range`` resume.
    """

    __table_args__ = (
        UniqueConstraint("update_version_id", "package_type", name="uq_update_packages_version_type"),
    )

    update_version_id: Mapped[int] = mapped_column(
        ForeignKey("update_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    package_type: Mapped[PackageType] = mapped_column(enum_column_type(PackageType), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_base64: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)


class UpdateTarget(ServerBaseModel):
    """Who one :class:`UpdateVersion` is offered to.

    Attributes:
        update_version_id: The version this target row applies to.
        scope: Whether this row targets every device or one specific
            device (see :class:`TargetScope`).
        target_device_public_id: The device's public UUID, as a
            string; set only when :attr:`scope` is
            :attr:`TargetScope.DEVICE`.
    """

    update_version_id: Mapped[int] = mapped_column(
        ForeignKey("update_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope: Mapped[TargetScope] = mapped_column(enum_column_type(TargetScope), nullable=False)
    target_device_public_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


class UpdateRollback(ServerBaseModel):
    """An append-only record of one rollback action against an :class:`UpdateVersion`.

    Attributes:
        update_version_id: The version that was rolled back.
        rolled_back_by: The administrator who performed the rollback.
        reason: An optional free-form explanation.
    """

    update_version_id: Mapped[int] = mapped_column(
        ForeignKey("update_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rolled_back_by: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class DeviceUpdateStatus(ServerBaseModel):
    """One device's current progress applying one specific version.

    Upserted by the Attendance Client itself (see
    ``POST /api/v1/updates/status``) — this server never infers a
    device's update state, only records what the device reports.

    Attributes:
        device_public_id: The reporting device's public UUID.
        update_version_id: Which version this status concerns.
        status: The device's current stage (see
            :class:`DeviceUpdateStatusValue`).
        progress_percent: Download progress, ``0``-``100``; meaningful
            only while :attr:`status` is
            :attr:`~DeviceUpdateStatusValue.DOWNLOADING`.
        error_message: Set when :attr:`status` is
            :attr:`~DeviceUpdateStatusValue.FAILED`.
        reported_at: When this row was last updated by the device.
    """

    __table_args__ = (
        UniqueConstraint(
            "device_public_id", "update_version_id", name="uq_device_update_status_device_version"
        ),
    )

    device_public_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    update_version_id: Mapped[int] = mapped_column(
        ForeignKey("update_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[DeviceUpdateStatusValue] = mapped_column(
        enum_column_type(DeviceUpdateStatusValue), nullable=False, index=True
    )
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class UpdateAuditEvent(ServerBaseModel):
    """An append-only log of one administrative action against an :class:`UpdateVersion`.

    Attributes:
        update_version_id: The version this action concerns.
        action: A short action code (e.g. ``"created"``,
            ``"package_uploaded"``, ``"published"``, ``"scheduled"``,
            ``"disabled"``, ``"rolled_back"``).
        performed_by: The administrator who performed the action.
        description: An optional free-form detail.
    """

    update_version_id: Mapped[int] = mapped_column(
        ForeignKey("update_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    performed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

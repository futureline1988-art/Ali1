"""Local software-update-check bookkeeping for the Attendance Client's own installation.

Mirrors :mod:`models.sync_state`'s shape and reasoning exactly: an
infrastructure-primitive table, extending :class:`~models.base.Base`
directly rather than :class:`~models.base.BaseModel`, since this is
not company-scoped business data (see that module's own docstring for
the identical argument applied to
:class:`~models.sync_state.ClientSyncCredential`/:class:`~models.sync_state.ClientSyncCursor`).

:class:`ClientUpdateState` holds one row per software update version
this installation has ever discovered — never deleted, so a small
local history survives (useful for "what did we already try and fail
on"), keyed by the server's own version id so re-discovering the same
version on a later check updates the existing row in place rather than
duplicating it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, UTCDateTime


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class ClientUpdateStatus(str, Enum):
    """One discovered update version's local lifecycle, from this installation's point of view.

    A package is only ever considered safe to install once it reaches
    :attr:`VERIFIED` — both :meth:`~updates.verifier.verify_checksum`
    and :meth:`~updates.verifier.verify_signature` passed. Nothing in
    this application ever transitions a row from :attr:`DOWNLOADING`
    straight to :attr:`INSTALLED`.
    """

    DISCOVERED = "discovered"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    VERIFIED = "verified"
    FAILED = "failed"
    POSTPONED = "postponed"
    INSTALLED = "installed"


class ClientUpdateState(Base):
    """This installation's local record of one software update version.

    Attributes:
        update_version_id: The server's own
            :class:`~server.models.update.UpdateVersion` id — the
            stable key a re-check upserts against.
        version: The version string (e.g. ``"1.2.0"``).
        update_type: How strongly this version should be pushed (see
            :class:`~updates.protocol.UpdateType`), as a plain string.
        release_notes: This version's release notes, if any.
        package_id: The server-side
            :class:`~server.models.update.UpdatePackage` id this
            installation chose to download (its own platform's
            package type — setup or portable, whichever this
            installation was built from).
        package_type: ``"setup"`` or ``"portable"``.
        checksum_sha256: The package's expected SHA-256, as reported
            by the server.
        signature_base64: The package's Ed25519 signature, as reported
            by the server.
        size_bytes: The package's expected size.
        status: This installation's current stage applying this
            version (see :class:`ClientUpdateStatus`).
        downloaded_bytes: How many bytes of the package have been
            downloaded so far — kept in sync with the on-disk
            ``.partial`` file's size (see
            :meth:`~updates.client.UpdatesApiClient.download_package`),
            so a resumed download's progress bar starts from the
            truth rather than zero.
        local_file_path: Where the (verified, complete) package file
            lives on disk, once :attr:`status` reaches
            :attr:`~ClientUpdateStatus.VERIFIED`.
        error_message: Set when :attr:`status` is
            :attr:`~ClientUpdateStatus.FAILED`.
        postponed_until: When a postponed, non-mandatory update should
            be offered again; ``None`` otherwise.
        discovered_at: When this version was first seen.
        updated_at: When this row was last modified.
    """

    __tablename__ = "client_update_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    update_version_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    update_type: Mapped[str] = mapped_column(String(20), nullable=False)
    release_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    package_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    package_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signature_base64: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ClientUpdateStatus.DISCOVERED.value
    )
    downloaded_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    local_file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    postponed_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

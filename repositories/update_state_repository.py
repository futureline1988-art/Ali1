"""Data access for the Attendance Client's local software-update-check bookkeeping.

Mirrors :mod:`repositories.sync_repository`'s shape exactly for the
same reason: a small, generic repository over one infrastructure
-primitive table (see :mod:`models.update_state`'s own docstring).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.update_state import ClientUpdateState, ClientUpdateStatus


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ClientUpdateStateRepository:
    """Data access for :class:`~models.update_state.ClientUpdateState`."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        self.session = session

    def get_by_version_id(self, update_version_id: int) -> ClientUpdateState | None:
        """Fetch this installation's local record of one server-side update version."""
        statement = select(ClientUpdateState).where(
            ClientUpdateState.update_version_id == update_version_id
        )
        return self.session.execute(statement).scalar_one_or_none()

    def get_latest(self) -> ClientUpdateState | None:
        """The most recently discovered update version's local record, if any."""
        statement = select(ClientUpdateState).order_by(ClientUpdateState.update_version_id.desc()).limit(1)
        return self.session.execute(statement).scalars().first()

    def list_all(self) -> list[ClientUpdateState]:
        """List every locally known update version, most recently discovered first."""
        statement = select(ClientUpdateState).order_by(ClientUpdateState.update_version_id.desc())
        return list(self.session.execute(statement).scalars().all())

    def upsert_discovered(
        self,
        *,
        update_version_id: int,
        version: str,
        update_type: str,
        release_notes: str | None,
        package_id: int | None,
        package_type: str | None,
        checksum_sha256: str | None,
        signature_base64: str | None,
        size_bytes: int | None,
    ) -> ClientUpdateState:
        """Create or refresh the local record for one discovered update version.

        Never regresses an already-further-along row's :attr:`~models.update_state.ClientUpdateState.status`
        back to ``discovered`` — re-checking and finding the same
        version again must not forget that it was already downloaded
        or verified.
        """
        row = self.get_by_version_id(update_version_id)
        if row is None:
            row = ClientUpdateState(
                update_version_id=update_version_id,
                version=version,
                update_type=update_type,
                release_notes=release_notes,
                package_id=package_id,
                package_type=package_type,
                checksum_sha256=checksum_sha256,
                signature_base64=signature_base64,
                size_bytes=size_bytes,
                status=ClientUpdateStatus.DISCOVERED.value,
            )
            self.session.add(row)
        else:
            row.version = version
            row.update_type = update_type
            row.release_notes = release_notes
            row.package_id = package_id
            row.package_type = package_type
            row.checksum_sha256 = checksum_sha256
            row.signature_base64 = signature_base64
            row.size_bytes = size_bytes
        self.session.flush()
        return row

    def update_progress(self, row: ClientUpdateState, *, status: str, downloaded_bytes: int) -> None:
        """Record download progress and the current status for ``row``."""
        row.status = status
        row.downloaded_bytes = downloaded_bytes
        self.session.flush()

    def mark_verified(self, row: ClientUpdateState, *, local_file_path: str) -> None:
        """Record that ``row``'s package has been downloaded and successfully verified."""
        row.status = ClientUpdateStatus.VERIFIED.value
        row.local_file_path = local_file_path
        row.error_message = None
        self.session.flush()

    def mark_failed(self, row: ClientUpdateState, *, error_message: str) -> None:
        """Record that ``row``'s download or verification failed."""
        row.status = ClientUpdateStatus.FAILED.value
        row.error_message = error_message
        self.session.flush()

    def mark_postponed(self, row: ClientUpdateState, *, until: datetime) -> None:
        """Record that the user postponed ``row`` until ``until``."""
        row.status = ClientUpdateStatus.POSTPONED.value
        row.postponed_until = until
        self.session.flush()

    def mark_installed(self, row: ClientUpdateState) -> None:
        """Record that ``row``'s package was handed off to the installer."""
        row.status = ClientUpdateStatus.INSTALLED.value
        self.session.flush()

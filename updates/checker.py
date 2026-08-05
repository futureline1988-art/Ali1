"""Check for, download, verify, and report on software updates.

Owns the whole client-side update lifecycle: ask a configured update
server what is assigned to this device, compare it against the running
version, download the matching package (resumable, with progress
reported back to the server), and verify it before ever marking it
safe to install.

Entirely optional and off by default: this application no longer
requires a central Attendance Server for normal operation, so there is
no automatic background scheduler driving this anymore (contrast the
retired ``sync.scheduler``) — a caller invokes
:meth:`UpdateCheckService.check_for_update` manually (e.g. a "Check for
Updates" action), and it raises :class:`DeviceNotEnrolledError`
whenever no :class:`~repositories.update_credential_repository.UpdateCredentialRepository`
row exists yet, which is the case for every installation until an
administrator manually configures one — see this package's own
``__init__.py``.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from database.database import Database
from models.update_state import ClientUpdateState, ClientUpdateStatus
from repositories.update_credential_repository import UpdateCredentialRepository
from repositories.update_state_repository import ClientUpdateStateRepository
from updates.client import UpdateConnectionError, UpdatesApiClient
from updates.protocol import UpdateType
from updates.verifier import verify_checksum, verify_signature

__all__ = [
    "DeviceNotEnrolledError",
    "CannotPostponeMandatoryUpdateError",
    "UpdateCheckService",
]


class DeviceNotEnrolledError(Exception):
    """Raised when no update-server credential has been configured for this installation yet."""


class CannotPostponeMandatoryUpdateError(Exception):
    """Raised by :meth:`UpdateCheckService.postpone` for a mandatory update."""


def _version_key(version: str) -> tuple[int, ...]:
    """Parse a dotted numeric version string into a comparable tuple.

    Byte-for-byte the same algorithm
    :func:`server.services.update_service._version_key` uses — this
    client replicates it rather than importing across the HTTP
    boundary.
    """
    return tuple(int(part) if part.isdigit() else 0 for part in re.split(r"[.\-]", version))


def is_newer_version(candidate: str, current: str) -> bool:
    """Whether ``candidate`` is a strictly newer version than ``current``."""
    return _version_key(candidate) > _version_key(current)


class UpdateCheckService:
    """Checks for, downloads, verifies, and reports on software updates for this installation."""

    def __init__(
        self,
        database: Database,
        server_url: str,
        *,
        current_version: str,
        package_type: str,
        downloads_dir: Path,
        public_key: Ed25519PublicKey,
        transport=None,
    ) -> None:
        """Create an update-check service.

        Args:
            database: This installation's own database.
            server_url: The Attendance Server's base URL.
            current_version: This running installation's own version
                (see :attr:`~config.AppConfig.app_version`).
            package_type: Which package type this installation should
                download — ``"setup"`` or ``"portable"``, matching how
                this build was distributed.
            downloads_dir: Where downloaded package files are stored.
            public_key: The embedded update-signing public key (see
                :mod:`updates.keys`), injected rather than always
                loaded from the module-level constant so tests can
                supply a throwaway keypair's public half.
            transport: Optional ``httpx`` transport override, for
                tests.
        """
        self._database = database
        self._server_url = server_url
        self._current_version = current_version
        self._package_type = package_type
        self._downloads_dir = downloads_dir
        self._public_key = public_key
        self._transport = transport

    def _build_client(self) -> UpdatesApiClient:
        with self._database.session_scope() as session:
            credential = UpdateCredentialRepository(session).get()
            if credential is None:
                raise DeviceNotEnrolledError(
                    "No update-server credential has been configured for this installation."
                )
            device_public_id = credential.device_public_id
            device_api_key = credential.api_key
            server_url = credential.server_url
        return UpdatesApiClient(
            server_url or self._server_url,
            device_public_id=device_public_id,
            device_api_key=device_api_key,
            transport=self._transport,
        )

    def check_for_update(self) -> ClientUpdateState | None:
        """Ask the server what is assigned to this device and record it if it is new.

        Returns:
            The local record for the newly (or already) discovered
            update, or ``None`` if nothing is assigned, the assigned
            version is not newer than :attr:`_current_version`, or no
            package matches this installation's own
            :attr:`_package_type`.

        Raises:
            DeviceNotEnrolledError: This installation has not enrolled
                yet.
            UpdateConnectionError: The server could not be reached.
        """
        client = self._build_client()
        try:
            assigned = client.get_assigned()
        finally:
            client.close()

        if assigned is None:
            return None
        if not is_newer_version(assigned.version.version, self._current_version):
            return None
        package = next((p for p in assigned.packages if p.package_type == self._package_type), None)
        if package is None:
            return None

        with self._database.session_scope() as session:
            return ClientUpdateStateRepository(session).upsert_discovered(
                update_version_id=assigned.version.id,
                version=assigned.version.version,
                update_type=assigned.version.update_type,
                release_notes=assigned.version.release_notes,
                package_id=package.id,
                package_type=package.package_type,
                checksum_sha256=package.checksum_sha256,
                signature_base64=package.signature_base64,
                size_bytes=package.size_bytes,
            )

    def download_and_verify(
        self, update_version_id: int, *, progress_callback: Callable[[int, int | None], None] | None = None
    ) -> bool:
        """Download (resumable), verify, and record the outcome for one discovered update.

        Never leaves a corrupted or unverified file where anything
        else in this application could mistake it for ready-to-install
        — a checksum or signature failure deletes the downloaded file
        and marks the local record
        :attr:`~models.update_state.ClientUpdateStatus.FAILED`.

        Returns:
            ``True`` if the package downloaded and verified
            successfully; ``False`` otherwise.
        """
        with self._database.session_scope() as session:
            repo = ClientUpdateStateRepository(session)
            state = repo.get_by_version_id(update_version_id)
            if state is None:
                return False
            package_id = state.package_id
            version = state.version
            package_type = state.package_type
            checksum = state.checksum_sha256
            signature = state.signature_base64
            repo.update_progress(state, status=ClientUpdateStatus.DOWNLOADING.value, downloaded_bytes=0)

        dest_path = self._downloads_dir / f"update_{version}_{package_type}.bin"

        def _on_progress(downloaded: int, total: int | None) -> None:
            with self._database.session_scope() as session:
                inner_repo = ClientUpdateStateRepository(session)
                inner_state = inner_repo.get_by_version_id(update_version_id)
                if inner_state is not None:
                    inner_repo.update_progress(
                        inner_state, status=ClientUpdateStatus.DOWNLOADING.value, downloaded_bytes=downloaded
                    )
            self._report_status_best_effort(
                update_version_id,
                status="downloading",
                progress_percent=int(downloaded * 100 / total) if total else 0,
            )
            if progress_callback is not None:
                progress_callback(downloaded, total)

        client = self._build_client()
        try:
            self._downloads_dir.mkdir(parents=True, exist_ok=True)
            client.download_package(package_id, dest_path, progress_callback=_on_progress)
        except UpdateConnectionError:
            self._fail(update_version_id, "تعذّر الاتصال بخادم الحضور أثناء التنزيل.")
            return False
        except Exception as exc:  # noqa: BLE001 - any download failure must not corrupt local state
            self._fail(update_version_id, str(exc))
            return False
        finally:
            client.close()

        with self._database.session_scope() as session:
            repo = ClientUpdateStateRepository(session)
            state = repo.get_by_version_id(update_version_id)
            repo.update_progress(state, status=ClientUpdateStatus.DOWNLOADED.value, downloaded_bytes=dest_path.stat().st_size)

        if not verify_checksum(dest_path, checksum or ""):
            dest_path.unlink(missing_ok=True)
            self._fail(update_version_id, "فشل التحقق من بصمة الحزمة (checksum) — الملف تالف.")
            return False
        if not verify_signature(dest_path, signature or "", self._public_key):
            dest_path.unlink(missing_ok=True)
            self._fail(update_version_id, "فشل التحقق من التوقيع الرقمي للحزمة.")
            return False

        with self._database.session_scope() as session:
            repo = ClientUpdateStateRepository(session)
            state = repo.get_by_version_id(update_version_id)
            repo.mark_verified(state, local_file_path=str(dest_path))
        self._report_status_best_effort(update_version_id, status="verified", progress_percent=100)
        return True

    def is_postponable(self, state: ClientUpdateState) -> bool:
        """Whether ``state`` may be postponed — every update type except mandatory."""
        return state.update_type != UpdateType.MANDATORY.value

    def postpone(self, update_version_id: int, *, until: datetime) -> None:
        """Postpone a discovered, non-mandatory update until ``until``.

        Raises:
            CannotPostponeMandatoryUpdateError: ``state.update_type``
                is :attr:`~updates.protocol.UpdateType.MANDATORY`.
        """
        with self._database.session_scope() as session:
            repo = ClientUpdateStateRepository(session)
            state = repo.get_by_version_id(update_version_id)
            if state is None:
                return
            if not self.is_postponable(state):
                raise CannotPostponeMandatoryUpdateError(
                    "لا يمكن تأجيل هذا التحديث لأنه إلزامي."
                )
            repo.mark_postponed(state, until=until)

    def _fail(self, update_version_id: int, message: str) -> None:
        with self._database.session_scope() as session:
            repo = ClientUpdateStateRepository(session)
            state = repo.get_by_version_id(update_version_id)
            if state is not None:
                repo.mark_failed(state, error_message=message)
        self._report_status_best_effort(update_version_id, status="failed", error_message=message)

    def _report_status_best_effort(
        self, update_version_id: int, *, status: str, progress_percent: int = 0, error_message: str | None = None
    ) -> None:
        """Report progress back to the server, swallowing any connection failure.

        Status reporting is informational for the Developer Dashboard
        (see :mod:`server.services.update_service`'s dashboard
        aggregation) — a failed report must never interrupt the
        download/verification this method is called alongside, which
        is exactly the "continue working offline" requirement applied
        to this feature.
        """
        try:
            client = self._build_client()
        except (DeviceNotEnrolledError, UpdateConnectionError):
            return
        try:
            client.report_status(
                update_version_id=update_version_id,
                status=status,
                progress_percent=progress_percent,
                error_message=error_message,
            )
        except Exception:  # noqa: BLE001 - status reporting is best-effort only
            pass
        finally:
            client.close()

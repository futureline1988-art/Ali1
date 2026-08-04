"""HTTP client for the Attendance Server's device-facing update-check and download endpoints.

Mirrors :mod:`sync.client`'s shape and error hierarchy — reusing its
:class:`~sync.client.SyncConnectionError`/:class:`~sync.client.SyncAuthError`/
:class:`~sync.client.SyncServerError` directly rather than duplicating
three near-identical exception classes, since both modules describe
exactly the same generic HTTP-transport failure modes against the same
authenticated Attendance Server, just for a different endpoint group
(see this package's own ``__init__.py``). Authenticated the same way
every sync pull already is (``X-Device-Id``/``X-Device-Api-Key``,
see :mod:`sync.coordinator`) — this is a second endpoint group on the
*same* device credential, not a second authentication mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from sync.client import SyncAuthError, SyncConnectionError, SyncServerError

_DEFAULT_TIMEOUT_SECONDS = 15.0
_DOWNLOAD_CHUNK_SIZE = 256 * 1024

__all__ = [
    "SyncConnectionError",
    "SyncAuthError",
    "SyncServerError",
    "UpdatePackageInfo",
    "UpdateVersionInfo",
    "AssignedUpdate",
    "UpdatesApiClient",
]


def _raise_for_response(response: httpx.Response) -> None:
    """Translate a non-2xx response into the appropriate error (mirrors :mod:`sync.client`'s own)."""
    if response.status_code == 401:
        raise SyncAuthError(f"Authentication rejected: {response.text}")
    if response.status_code >= 400:
        raise SyncServerError(f"{response.status_code} from {response.request.url}: {response.text}")


@dataclass(frozen=True)
class UpdatePackageInfo:
    """One package (setup or portable) belonging to an update version."""

    id: int
    package_type: str
    checksum_sha256: str
    signature_base64: str
    size_bytes: int

    @classmethod
    def from_json(cls, data: dict) -> "UpdatePackageInfo":
        return cls(
            id=data["id"],
            package_type=data["package_type"],
            checksum_sha256=data["checksum_sha256"],
            signature_base64=data["signature_base64"],
            size_bytes=data["size_bytes"],
        )


@dataclass(frozen=True)
class UpdateVersionInfo:
    """One software update version, as returned by the device-facing update endpoints."""

    id: int
    version: str
    release_notes: str | None
    min_supported_version: str | None
    update_type: str
    publish_status: str

    @classmethod
    def from_json(cls, data: dict) -> "UpdateVersionInfo":
        return cls(
            id=data["id"],
            version=data["version"],
            release_notes=data.get("release_notes"),
            min_supported_version=data.get("min_supported_version"),
            update_type=data["update_type"],
            publish_status=data["publish_status"],
        )


@dataclass(frozen=True)
class AssignedUpdate:
    """The update version actually targeted at this device, with its downloadable packages."""

    version: UpdateVersionInfo
    packages: list[UpdatePackageInfo] = field(default_factory=list)


class UpdatesApiClient:
    """A thin client for one device's update-check, download, and status-report calls."""

    def __init__(
        self,
        base_url: str,
        *,
        device_public_id: str,
        device_api_key: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Create a client bound to one device's credentials.

        Args:
            base_url: The Attendance Server's base URL.
            device_public_id: This device's UUID (``X-Device-Id``).
            device_api_key: This device's plaintext sync credential
                (``X-Device-Api-Key``) — the same credential
                :class:`~sync.client.SyncClient` uses.
            transport: Optional ``httpx`` transport override, for
                tests.
            timeout: Request timeout in seconds.
        """
        self._client = httpx.Client(
            base_url=base_url,
            transport=transport,
            timeout=timeout,
            headers={"X-Device-Id": device_public_id, "X-Device-Api-Key": device_api_key},
        )

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> "UpdatesApiClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get_latest(self) -> UpdateVersionInfo | None:
        """Fetch the highest-version currently-live update, ignoring per-device targeting."""
        response = self._get("/api/v1/updates/latest")
        data = response.json()["version"]
        return UpdateVersionInfo.from_json(data) if data is not None else None

    def get_assigned(self) -> AssignedUpdate | None:
        """Fetch the update actually targeted at this device, if any, with its packages."""
        response = self._get("/api/v1/updates/assigned")
        data = response.json()
        if data["version"] is None:
            return None
        return AssignedUpdate(
            version=UpdateVersionInfo.from_json(data["version"]),
            packages=[UpdatePackageInfo.from_json(item) for item in data["packages"]],
        )

    def get_history(self) -> list[UpdateVersionInfo]:
        """Fetch every published/scheduled/disabled/rolled-back version."""
        response = self._get("/api/v1/updates/history")
        return [UpdateVersionInfo.from_json(item) for item in response.json()["versions"]]

    def report_status(
        self,
        *,
        update_version_id: int,
        status: str,
        progress_percent: int = 0,
        error_message: str | None = None,
    ) -> None:
        """Report this device's current progress applying a specific update version."""
        try:
            response = self._client.post(
                "/api/v1/updates/status",
                json={
                    "update_version_id": update_version_id,
                    "status": status,
                    "progress_percent": progress_percent,
                    "error_message": error_message,
                },
            )
        except httpx.TransportError as exc:
            raise SyncConnectionError(f"Could not reach the Attendance Server: {exc}") from exc
        _raise_for_response(response)

    def download_package(
        self,
        package_id: int,
        dest_path: Path,
        *,
        resume: bool = True,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> None:
        """Download one package to ``dest_path``, resuming a previous partial download.

        Writes to ``dest_path`` with a ``.partial`` suffix while in
        progress, renaming to the final name only once the entire
        download has completed successfully — a caller inspecting
        ``dest_path`` for existence therefore never observes a
        truncated file (see this package's own "never install
        corrupted packages" requirement, whose second half —
        checksum/signature verification — is :mod:`updates.verifier`'s
        job, run only after this method returns).

        Args:
            package_id: Which package to download (see
                :attr:`UpdatePackageInfo.id`).
            dest_path: Where the completed file should end up.
            resume: If ``True`` and a ``.partial`` file already exists
                at this path, resume from its current size via an
                HTTP ``Range`` request instead of starting over.
            progress_callback: Optional ``(downloaded_bytes,
                total_bytes_or_none)`` callback, invoked after every
                chunk written.

        Raises:
            SyncConnectionError: The server could not be reached.
            SyncAuthError: This device's credential was rejected.
            SyncServerError: Any other non-2xx response.
        """
        partial_path = dest_path.with_name(dest_path.name + ".partial")
        existing_size = partial_path.stat().st_size if resume and partial_path.exists() else 0
        headers = {"Range": f"bytes={existing_size}-"} if existing_size else {}

        try:
            with self._client.stream(
                "GET", f"/api/v1/updates/packages/{package_id}/download", headers=headers
            ) as response:
                if response.status_code == 416:
                    # The server considers our resume offset invalid (e.g. the
                    # package changed size) - discard the stale partial file
                    # and restart the download from scratch.
                    partial_path.unlink(missing_ok=True)
                    response.close()
                    return self.download_package(
                        package_id, dest_path, resume=False, progress_callback=progress_callback
                    )
                _raise_for_response(response)

                resumed = existing_size > 0 and response.status_code == 206
                mode = "ab" if resumed else "wb"
                downloaded = existing_size if resumed else 0

                total: int | None = None
                content_range = response.headers.get("content-range")
                if content_range and "/" in content_range:
                    total = int(content_range.rsplit("/", 1)[-1])
                elif "content-length" in response.headers:
                    total = downloaded + int(response.headers["content-length"])

                partial_path.parent.mkdir(parents=True, exist_ok=True)
                with partial_path.open(mode) as handle:
                    for chunk in response.iter_bytes(chunk_size=_DOWNLOAD_CHUNK_SIZE):
                        handle.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback is not None:
                            progress_callback(downloaded, total)
        except httpx.TransportError as exc:
            raise SyncConnectionError(f"Could not reach the Attendance Server: {exc}") from exc

        partial_path.replace(dest_path)

    def _get(self, path: str) -> httpx.Response:
        try:
            response = self._client.get(path)
        except httpx.TransportError as exc:
            raise SyncConnectionError(f"Could not reach the Attendance Server: {exc}") from exc
        _raise_for_response(response)
        return response

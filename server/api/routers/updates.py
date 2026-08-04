"""Software update distribution: admin management + device-facing endpoints.

Two audiences, two authentication schemes, exactly like
:mod:`server.api.routers.sync` already splits push/pull (device
credentials) from conflict management (admin bearer token):

* The Developer Suite manages versions, packages, targeting, and
  publish/schedule/disable/rollback — gated behind
  ``require_scope("sync:admin")`` (see
  :mod:`server.auth.dependencies`), the same scope every other
  administrative endpoint on this server already uses.
* The Attendance Client checks for and downloads updates, and reports
  its own progress back — authenticated the same way push/pull are
  (see :mod:`server.auth.device_auth`), reusing the exact same
  long-lived device credential rather than inventing a second
  authentication mechanism for one more endpoint group.

Package upload (:func:`upload_package`) and download
(:func:`download_package`) both use a raw request/response body with
metadata in custom headers, the same convention
:mod:`server.auth.device_auth` already established for
``X-Device-Id``/``X-Device-Api-Key`` — deliberately not
``multipart/form-data``, which would add a new dependency
(``python-multipart``) this server does not otherwise need.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from server.api.schemas import (
    CreateUpdateVersionRequest,
    ReportUpdateStatusRequest,
    RollbackUpdateRequest,
    ScheduleUpdateRequest,
    SetUpdateTargetsRequest,
)
from server.auth.dependencies import AuthenticatedPrincipal, require_scope
from server.auth.device_auth import get_authenticated_device
from server.models.device import SyncDevice
from server.models.update import PackageType
from server.repositories.admin_account_repository import AdminAccountRepository
from server.services.update_service import (
    ChecksumMismatchError,
    DuplicateVersionError,
    NoPackageUploadedError,
    UpdateService,
    UpdateVersionNotFoundError,
)

router = APIRouter(prefix="/api/v1/updates", tags=["updates"])

_DOWNLOAD_CHUNK_SIZE = 1024 * 1024


def _resolve_actor(request: Request, principal: AuthenticatedPrincipal) -> str:
    """Resolve a human-readable "who did this" string for an audit event.

    Looks up the calling admin account's username by the token's
    ``principal_id`` (its public UUID); falls back to the raw
    ``principal_id`` itself when it is not a UUID or no matching
    account exists (e.g. a test-issued or bootstrap token not backed
    by a real :class:`~server.models.admin_account.AdminAccount` row —
    the same tolerance
    :mod:`server.api.routers.devices` already assumes is fine for any
    ``sync:admin``-scoped caller).
    """
    try:
        account_public_id = uuid.UUID(principal.principal_id)
    except ValueError:
        return principal.principal_id
    with request.app.state.container.database.session_scope() as session:
        account = AdminAccountRepository(session).get_by_public_id(account_public_id)
        return account.username if account is not None else principal.principal_id


def _version_or_404(update_service: UpdateService, update_version_id: int):
    version = update_service.get_version(update_version_id)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such update version.")
    return version


# ---------------------------------------------------------------------------
# Admin: version lifecycle.
# ---------------------------------------------------------------------------


@router.post("/versions", status_code=201)
def create_version(
    body: CreateUpdateVersionRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin")),
) -> dict:
    """Create a new, draft update version."""
    update_service: UpdateService = request.app.state.container.update_service
    try:
        version = update_service.create_version(
            version=body.version,
            release_notes=body.release_notes,
            min_supported_version=body.min_supported_version,
            update_type=body.update_type,
            created_by=_resolve_actor(request, principal),
        )
    except DuplicateVersionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return version.to_dict()


@router.get("/versions")
def list_versions(
    request: Request,
    _principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin", "sync:read")),
) -> dict:
    """List every update version regardless of status — the Developer Suite's history view."""
    update_service: UpdateService = request.app.state.container.update_service
    return {"versions": [v.to_dict() for v in update_service.list_versions()]}


@router.get("/versions/{update_version_id}")
def get_version(
    update_version_id: int,
    request: Request,
    _principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin", "sync:read")),
) -> dict:
    """Fetch one version's full detail: metadata, packages, targets, and audit history."""
    update_service: UpdateService = request.app.state.container.update_service
    version = _version_or_404(update_service, update_version_id)
    packages = update_service.get_packages(update_version_id)
    with request.app.state.container.database.session_scope() as session:
        from server.repositories.update_repository import (
            UpdateRollbackRepository,
            UpdateTargetRepository,
            UpdateVersionRepository,
        )

        targets = UpdateTargetRepository(session).list_for_version(update_version_id)
        rollbacks = UpdateRollbackRepository(session).list_for_version(update_version_id)
        audit_events = UpdateVersionRepository(session).list_audit_events(update_version_id)
        return {
            "version": version.to_dict(),
            "packages": [p.to_dict() for p in packages],
            "targets": [t.to_dict() for t in targets],
            "rollbacks": [r.to_dict() for r in rollbacks],
            "audit_events": [e.to_dict() for e in audit_events],
        }


@router.post("/versions/{update_version_id}/packages", status_code=201)
async def upload_package(
    update_version_id: int,
    request: Request,
    x_package_type: str = Header(...),
    x_checksum_sha256: str = Header(...),
    x_signature_base64: str = Header(...),
    x_original_filename: str = Header(default="package.bin"),
    principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin")),
) -> dict:
    """Upload a setup or portable package for a version.

    The request body is the raw package file bytes, read directly via
    :meth:`~starlette.requests.Request.body` rather than a typed
    ``bytes`` parameter (see this module's docstring for why not
    ``multipart/form-data``). The Developer Suite computes
    ``X-Checksum-Sha256`` and signs the file with its own
    update-signing private key to produce ``X-Signature-Base64``
    *before* calling this endpoint (see :mod:`licensing.crypto.signing`)
    — this server only re-verifies the checksum and stores the
    signature; it never signs anything itself.
    """
    update_service: UpdateService = request.app.state.container.update_service
    body = await request.body()
    try:
        package_type = PackageType(x_package_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid package type: {x_package_type!r}."
        ) from exc

    try:
        package = update_service.add_package(
            update_version_id,
            package_type=package_type,
            file_bytes=body,
            claimed_checksum_sha256=x_checksum_sha256,
            signature_base64=x_signature_base64,
            original_filename=x_original_filename,
            performed_by=_resolve_actor(request, principal),
        )
    except UpdateVersionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ChecksumMismatchError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return package.to_dict(exclude={"signature_base64"})


@router.put("/versions/{update_version_id}/targets")
def set_targets(
    update_version_id: int,
    body: SetUpdateTargetsRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin")),
) -> dict:
    """Replace a version's current targeting (all devices, or a specific list)."""
    update_service: UpdateService = request.app.state.container.update_service
    try:
        targets = update_service.set_targets(
            update_version_id,
            scope=body.scope,
            device_public_ids=body.device_public_ids,
            performed_by=_resolve_actor(request, principal),
        )
    except UpdateVersionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"targets": [t.to_dict() for t in targets]}


@router.post("/versions/{update_version_id}/publish")
def publish_version(
    update_version_id: int,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin")),
) -> dict:
    """Publish a version immediately."""
    update_service: UpdateService = request.app.state.container.update_service
    try:
        version = update_service.publish(update_version_id, performed_by=_resolve_actor(request, principal))
    except UpdateVersionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except NoPackageUploadedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return version.to_dict()


@router.post("/versions/{update_version_id}/schedule")
def schedule_version(
    update_version_id: int,
    body: ScheduleUpdateRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin")),
) -> dict:
    """Schedule a version to become available at a future time."""
    update_service: UpdateService = request.app.state.container.update_service
    scheduled_at = body.scheduled_at
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    try:
        version = update_service.schedule(
            update_version_id, scheduled_at=scheduled_at, performed_by=_resolve_actor(request, principal)
        )
    except UpdateVersionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except NoPackageUploadedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return version.to_dict()


@router.post("/versions/{update_version_id}/disable")
def disable_version(
    update_version_id: int,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin")),
) -> dict:
    """Disable a version, immediately removing it from every latest/assigned response."""
    update_service: UpdateService = request.app.state.container.update_service
    try:
        version = update_service.disable(update_version_id, performed_by=_resolve_actor(request, principal))
    except UpdateVersionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return version.to_dict()


@router.post("/versions/{update_version_id}/rollback")
def rollback_version(
    update_version_id: int,
    body: RollbackUpdateRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin")),
) -> dict:
    """Roll back a version: never deletes it, only excludes it from future offers."""
    update_service: UpdateService = request.app.state.container.update_service
    try:
        version = update_service.rollback(
            update_version_id, performed_by=_resolve_actor(request, principal), reason=body.reason
        )
    except UpdateVersionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return version.to_dict()


@router.get("/stats")
def get_stats(
    request: Request,
    _principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin", "sync:read")),
) -> dict:
    """Aggregate update-distribution statistics for the Developer Dashboard."""
    update_service: UpdateService = request.app.state.container.update_service
    stats = update_service.get_dashboard_stats()
    return {
        "latest_deployed_version": stats.latest_deployed_version,
        "companies_per_version": stats.companies_per_version,
        "pending_count": stats.pending_count,
        "failed_count": stats.failed_count,
        "successful_count": stats.successful_count,
        "average_download_progress_percent": stats.average_download_progress_percent,
    }


# ---------------------------------------------------------------------------
# Device-facing: check, download, report.
# ---------------------------------------------------------------------------


@router.get("/latest")
def get_latest(request: Request, _device: SyncDevice = Depends(get_authenticated_device)) -> dict:
    """The highest-version currently-live update, ignoring per-device targeting."""
    update_service: UpdateService = request.app.state.container.update_service
    version = update_service.get_latest_global()
    return {"version": version.to_dict() if version is not None else None}


@router.get("/assigned")
def get_assigned(request: Request, device: SyncDevice = Depends(get_authenticated_device)) -> dict:
    """The update actually targeted at the calling device, if any, with its packages."""
    update_service: UpdateService = request.app.state.container.update_service
    version = update_service.get_assigned_for_device(str(device.public_id))
    if version is None:
        return {"version": None, "packages": []}
    packages = update_service.get_packages(version.id)
    return {"version": version.to_dict(), "packages": [p.to_dict() for p in packages]}


@router.get("/history")
def get_history(request: Request, _device: SyncDevice = Depends(get_authenticated_device)) -> dict:
    """Every published/scheduled/disabled/rolled-back version, for the client's own display."""
    update_service: UpdateService = request.app.state.container.update_service
    return {"versions": [v.to_dict() for v in update_service.list_history()]}


@router.get("/packages/{package_id}/download")
def download_package(
    package_id: int,
    request: Request,
    range_header: str | None = Header(default=None, alias="range"),
    _device: SyncDevice = Depends(get_authenticated_device),
) -> Response:
    """Download one package's file, honoring an HTTP ``Range`` header for resumable downloads.

    Starlette's built-in ``FileResponse`` has no ``Range`` support (as
    of the version this project pins), so this is implemented
    directly: a bare request returns the whole file as ``200``; a
    ``Range: bytes=start-end`` request returns just that slice as
    ``206 Partial Content`` with ``Content-Range``, letting the
    Attendance Client resume an interrupted download without
    re-fetching bytes it already has.
    """
    update_service: UpdateService = request.app.state.container.update_service
    package = update_service.get_package(package_id)
    if package is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such package.")
    file_path = update_service.get_package_file_path(package)
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Package file is missing on disk.")

    file_size = file_path.stat().st_size
    start, end = 0, file_size - 1
    status_code = status.HTTP_200_OK
    headers = {
        "Accept-Ranges": "bytes",
        "X-Checksum-Sha256": package.checksum_sha256,
        "X-Signature-Base64": package.signature_base64,
        "Content-Disposition": f'attachment; filename="{file_path.name}"',
    }

    if range_header and range_header.startswith("bytes="):
        try:
            range_spec = range_header.removeprefix("bytes=")
            start_str, _, end_str = range_spec.partition("-")
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else file_size - 1
        except ValueError:
            start, end = 0, file_size - 1
        else:
            start = max(0, min(start, file_size - 1))
            end = max(start, min(end, file_size - 1))
            status_code = status.HTTP_206_PARTIAL_CONTENT
            headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    content_length = end - start + 1

    def _iter_file():
        with file_path.open("rb") as handle:
            handle.seek(start)
            remaining = content_length
            while remaining > 0:
                chunk = handle.read(min(_DOWNLOAD_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers["Content-Length"] = str(content_length)
    return StreamingResponse(
        _iter_file(), status_code=status_code, media_type="application/octet-stream", headers=headers
    )


@router.post("/status")
def report_status(
    body: ReportUpdateStatusRequest,
    request: Request,
    device: SyncDevice = Depends(get_authenticated_device),
) -> dict:
    """Report this device's current progress applying a specific update version."""
    update_service: UpdateService = request.app.state.container.update_service
    try:
        row = update_service.report_device_status(
            device_public_id=str(device.public_id),
            update_version_id=body.update_version_id,
            status=body.status,
            progress_percent=body.progress_percent,
            error_message=body.error_message,
        )
    except UpdateVersionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return row.to_dict()

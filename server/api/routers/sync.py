"""Push, pull, and conflict resolution.

Push and pull are authenticated by device credentials (see
:mod:`server.auth.device_auth`) — a device's own long-lived secret, not
an admin's short-lived bearer token. Conflict listing/resolution is an
administrative action instead, gated behind ``require_scope("sync:admin")``
like device registration.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from server.api.schemas import PushRequest, ResolveConflictRequest
from server.auth.dependencies import AuthenticatedPrincipal, require_scope
from server.auth.device_auth import get_authenticated_device
from server.models.device import SyncDevice
from server.services.sync_service import (
    ChangeInput,
    ChangeNotInConflictError,
    ChangeRecordNotFoundError,
    SyncService,
)

router = APIRouter(prefix="/api/v1/sync", tags=["sync"])

_MAX_PULL_LIMIT = 500


@router.post("/push")
def push_changes(
    body: PushRequest, request: Request, device: SyncDevice = Depends(get_authenticated_device)
) -> dict:
    """Push a batch of local changes; returns one result per change, in order.

    See :meth:`~server.services.sync_service.SyncService.push_changes`
    for exactly how each change is checksummed, conflict-checked, and
    applied.
    """
    sync_service: SyncService = request.app.state.container.sync_service
    changes = [
        ChangeInput(
            entity_type=item.entity_type,
            entity_id=item.entity_id,
            operation=item.operation,
            payload=item.payload,
            checksum=item.checksum,
            base_version=item.base_version,
        )
        for item in body.changes
    ]
    results = sync_service.push_changes(device.id, changes)
    return {
        "results": [
            {
                "entity_type": result.entity_type,
                "entity_id": result.entity_id,
                "status": result.status.value,
                "new_version": result.new_version,
                "conflict_reason": result.conflict_reason,
                "change_record_id": result.change_record_id,
            }
            for result in results
        ]
    }


@router.get("/pull")
def pull_changes(
    request: Request,
    since: int = 0,
    entity_type: str | None = None,
    limit: int = 100,
    device: SyncDevice = Depends(get_authenticated_device),
) -> dict:
    """Pull applied changes after ``since``, one batch at a time.

    Args:
        since: Resume after this change id (``0`` for the beginning).
        entity_type: Optionally restrict to one entity type.
        limit: Batch size, clamped to ``[1, 500]``.
    """
    sync_service: SyncService = request.app.state.container.sync_service
    clamped_limit = max(1, min(limit, _MAX_PULL_LIMIT))
    changes, next_cursor = sync_service.pull_changes(
        since, entity_type=entity_type, limit=clamped_limit
    )
    return {
        "changes": [change.to_dict() for change in changes],
        "next_cursor": next_cursor,
    }


@router.get("/conflicts")
def list_conflicts(
    request: Request,
    device_id: int | None = None,
    _principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin")),
) -> dict:
    """List unresolved conflicts, optionally for one device."""
    sync_service: SyncService = request.app.state.container.sync_service
    conflicts = sync_service.list_conflicts(device_id=device_id)
    return {"conflicts": [conflict.to_dict() for conflict in conflicts]}


_MAX_ACTIVITY_LIMIT = 200


@router.get("/activity")
def list_recent_activity(
    request: Request,
    limit: int = 50,
    _principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin")),
) -> dict:
    """List the most recent change records of any status, for a monitoring dashboard.

    Read-only, administrative (``sync:admin``-scoped, like
    ``/conflicts``): reuses :meth:`~server.services.sync_service.SyncService.list_recent_activity`
    unmodified — no new write path, no new business logic, only a
    broader read shape over the same ledger ``/push``/``/pull`` already
    use.
    """
    sync_service: SyncService = request.app.state.container.sync_service
    clamped_limit = max(1, min(limit, _MAX_ACTIVITY_LIMIT))
    changes = sync_service.list_recent_activity(limit=clamped_limit)
    return {"changes": [change.to_dict() for change in changes]}


@router.post("/conflicts/{change_id}/resolve")
def resolve_conflict(
    change_id: int,
    body: ResolveConflictRequest,
    request: Request,
    _principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin")),
) -> dict:
    """Force-apply or discard a conflicting change."""
    sync_service: SyncService = request.app.state.container.sync_service
    try:
        record = sync_service.resolve_conflict(change_id, apply_incoming=body.apply_incoming)
    except ChangeRecordNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ChangeNotInConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return record.to_dict()

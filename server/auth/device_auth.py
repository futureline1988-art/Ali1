"""FastAPI dependency authenticating a device for sync push/pull calls.

Deliberately separate from :mod:`server.auth.dependencies`'
``AuthenticatedPrincipal``/``get_current_principal``, which represent a
short-lived, interactive bearer token (an admin caller, e.g. a future
Developer Suite login). A device's sync credential is instead a
long-lived secret issued once at registration (see
:mod:`server.services.device_service`) and never refreshed — the right
shape for offline-first sync, where a device may not be able to reach
the server to renew a short-lived token before it needs to push
whatever it queued while disconnected.
"""

from __future__ import annotations

import uuid

from fastapi import Header, HTTPException, Request, status

from server.models.device import SyncDevice
from server.services.device_service import DeviceService


def get_authenticated_device(
    request: Request,
    x_device_id: str | None = Header(default=None),
    x_device_api_key: str | None = Header(default=None),
) -> SyncDevice:
    """Resolve and verify the calling device from its id/api-key header pair.

    Args:
        request: The current request, used only to reach this server's
            :class:`~server.container.ServiceContainer` (for its
            ``device_service``).
        x_device_id: The ``X-Device-Id`` header — the device's public
            UUID, as issued at registration.
        x_device_api_key: The ``X-Device-Api-Key`` header — the
            device's plaintext sync credential, as issued at
            registration.

    Returns:
        The authenticated device.

    Raises:
        HTTPException: 401 if either header is missing, ``x_device_id``
            is not a valid UUID, or the credential does not verify.
    """
    if not x_device_id or not x_device_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Device-Id or X-Device-Api-Key header.",
        )
    try:
        device_public_id = uuid.UUID(x_device_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="X-Device-Id is not a valid UUID."
        ) from exc

    device_service: DeviceService = request.app.state.container.device_service
    device = device_service.authenticate_device(device_public_id, x_device_api_key)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid device credentials."
        )
    return device

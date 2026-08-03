"""Admin authentication: login, refresh, logout, password change/reset, sessions.

Mounted at ``/api/v1/auth`` — a distinct prefix from ``/api/v1/sync``
and ``/api/v1/devices``, deliberately isolated from the customer
synchronization surface: nothing in this router touches
:class:`~server.services.sync_service.SyncService`/
:class:`~server.services.device_service.DeviceService`, and nothing in
those services knows this router exists. Every route here delegates
to :class:`~server.services.admin_auth_service.AdminAuthService`
— no auth/session/password logic is duplicated here, only HTTP
request/response shaping and exception-to-status-code translation.

``/login``, ``/refresh``, and the two ``/password-reset`` routes are
unauthenticated by design (that is the point of a login/refresh/reset
flow — proving identity *is* the request). ``/logout`` only requires
possessing the refresh token being revoked, the same "possession is
the credential" model :mod:`server.auth.device_auth` already uses for
device credentials. ``/change-password`` and ``/sessions`` require a
valid access token, verified through the existing, unmodified
:func:`~server.auth.dependencies.get_current_principal`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from server.api.schemas import (
    AdminChangePasswordRequest,
    AdminLoginRequest,
    AdminLogoutRequest,
    AdminPasswordResetCompleteRequest,
    AdminPasswordResetRequestBody,
    AdminRefreshRequest,
)
from server.auth.dependencies import AuthenticatedPrincipal, get_current_principal, require_scope
from server.services.admin_auth_service import (
    AccountLockedError,
    AccountNotFoundError,
    AdminAuthenticationError,
    AdminAuthService,
    AuthResult,
    InvalidResetTokenError,
    InvalidRefreshTokenError,
    PasswordPolicyError,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _auth_result_response(result: AuthResult) -> dict:
    return {
        "access_token": result.access_token,
        "refresh_token": result.refresh_token,
        "token_type": "bearer",
        "expires_in_minutes": result.expires_in_minutes,
        "account": result.account.to_dict(exclude={"password_hash"}),
    }


@router.post("/login")
def login(body: AdminLoginRequest, request: Request) -> dict:
    """Authenticate a username/password pair and start a new session.

    Returns:
        ``{access_token, refresh_token, token_type, expires_in_minutes, account}``.

    Raises:
        HTTPException: 423 if the account is locked, 401 for any other
            authentication failure.
    """
    service: AdminAuthService = request.app.state.container.admin_auth_service
    user_agent = body.user_agent or request.headers.get("user-agent")
    try:
        result = service.login(body.username, body.password, user_agent=user_agent)
    except AccountLockedError as exc:
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=str(exc)) from exc
    except AdminAuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return _auth_result_response(result)


@router.post("/refresh")
def refresh(body: AdminRefreshRequest, request: Request) -> dict:
    """Exchange a refresh token for a new access token, rotating the refresh token too.

    Returns:
        ``{access_token, refresh_token, token_type, expires_in_minutes, account}``.

    Raises:
        HTTPException: 401 if the refresh token is invalid, expired, or revoked.
    """
    service: AdminAuthService = request.app.state.container.admin_auth_service
    try:
        result = service.refresh(body.refresh_token)
    except InvalidRefreshTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return _auth_result_response(result)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: AdminLogoutRequest, request: Request) -> Response:
    """Revoke one session by its refresh token.

    Always succeeds, including for an already-invalid token (see
    :meth:`~server.services.admin_auth_service.AdminAuthService.logout`'s
    own docstring for why).
    """
    service: AdminAuthService = request.app.state.container.admin_auth_service
    service.logout(body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: AdminChangePasswordRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
) -> Response:
    """Change the authenticated account's own password, revoking every existing session.

    Raises:
        HTTPException: 401 if the current password is incorrect or the
            token does not belong to an admin account, 422 if the new
            password fails the configured strength policy.
    """
    service: AdminAuthService = request.app.state.container.admin_auth_service
    account_public_id = _require_admin_account_principal(principal)
    try:
        service.change_password(
            account_public_id, current_password=body.current_password, new_password=body.new_password
        )
    except AdminAuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sessions")
def list_sessions(
    request: Request, principal: AuthenticatedPrincipal = Depends(get_current_principal)
) -> dict:
    """List the authenticated account's own currently active sessions."""
    service: AdminAuthService = request.app.state.container.admin_auth_service
    account_public_id = _require_admin_account_principal(principal)
    try:
        sessions = service.list_sessions(account_public_id)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return {
        "sessions": [
            session_row.to_dict(exclude={"refresh_token_hash"}) for session_row in sessions
        ]
    }


_MAX_AUDIT_LOG_LIMIT = 200


@router.get("/audit-log")
def list_audit_log(
    request: Request,
    limit: int = 50,
    _principal: AuthenticatedPrincipal = Depends(require_scope("sync:admin", "sync:read")),
) -> dict:
    """List the most recent admin authentication audit events, for a monitoring dashboard.

    Read-only, like the Phase 10 ``sync:read``-widened endpoints
    (``/api/v1/devices``, ``/api/v1/sync/activity``, ``/api/v1/status``)
    — reuses :meth:`~server.services.admin_auth_service.AdminAuthService.list_audit_log`
    unmodified; no new logging path, only a broader read shape over the
    audit trail every other route on this router already appends to.
    """
    service: AdminAuthService = request.app.state.container.admin_auth_service
    clamped_limit = max(1, min(limit, _MAX_AUDIT_LOG_LIMIT))
    entries = service.list_audit_log(limit=clamped_limit)
    return {"entries": [entry.to_dict() for entry in entries]}


@router.post("/password-reset/request")
def request_password_reset(body: AdminPasswordResetRequestBody, request: Request) -> dict:
    """Issue a password reset token for an account, if it exists.

    Always returns the same shape regardless of whether the username
    is registered — see
    :meth:`~server.services.admin_auth_service.AdminAuthService.request_password_reset`'s
    own docstring for why. ``reset_token`` is ``null`` when there is
    nothing to reset (nothing was delivered anywhere, since no
    delivery mechanism exists yet — see this router's module
    docstring on "infrastructure only").
    """
    service: AdminAuthService = request.app.state.container.admin_auth_service
    reset_token = service.request_password_reset(body.username)
    return {"reset_token": reset_token}


@router.post("/password-reset/complete", status_code=status.HTTP_204_NO_CONTENT)
def complete_password_reset(body: AdminPasswordResetCompleteRequest, request: Request) -> Response:
    """Redeem a password reset token, setting a new password.

    Raises:
        HTTPException: 400 if the token is invalid, expired, or
            already used; 422 if the new password fails the configured
            strength policy.
    """
    service: AdminAuthService = request.app.state.container.admin_auth_service
    try:
        service.complete_password_reset(body.reset_token, body.new_password)
    except InvalidResetTokenError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _require_admin_account_principal(principal: AuthenticatedPrincipal) -> uuid.UUID:
    """Validate that ``principal`` was minted for an admin account and return its public id.

    Raises:
        HTTPException: 403 if the token belongs to some other kind of
            principal (e.g. a future customer-application token type).
    """
    if principal.principal_type != "admin_account":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="This endpoint requires an admin account token."
        )
    try:
        return uuid.UUID(principal.principal_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has a malformed principal id."
        ) from exc

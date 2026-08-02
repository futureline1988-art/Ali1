"""Shared FastAPI dependencies: DB sessions, bearer-token auth, and RBAC.

Mirrors ``controllers/base_controller.py``'s design on purpose — one
request is one unit of work (:func:`get_db_session`, matching
``BaseController._run``'s one-session-per-operation rule), and a route
is gated behind a permission code the same way a controller method is
gated behind :func:`~controllers.base_controller.requires_permission`
(:func:`require_permission` here). The two RBAC gates share the same
source of truth — a user's :class:`~models.role.Role` — so a
permission granted through the Settings screen's role editor takes
effect on both the desktop app and this API without any separate
configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from database.database import session_scope
from utils.security import TokenError, verify_signed_token

_ACCESS_DENIED_MESSAGE_AR = "ليس لديك صلاحية للقيام بهذا الإجراء."


def get_db_session() -> Iterator[Session]:
    """Yield a request-scoped database session.

    Commits when the route handler returns normally, rolls back if it
    raises — the exact transactional shape as
    :func:`~database.database.session_scope`, which this simply
    delegates to.
    """
    with session_scope() as session:
        yield session


@dataclass(frozen=True)
class CurrentUser:
    """The authenticated caller, resolved once per request from their bearer token."""

    user_id: int
    company_id: int
    permission_codes: frozenset[str]


def get_current_user(
    authorization: str | None = Header(default=None),
) -> CurrentUser:
    """Resolve and verify the caller's bearer token.

    Args:
        authorization: The raw ``Authorization`` header, expected in
            ``"Bearer <token>"`` form.

    Returns:
        The authenticated caller's identity and granted permission
        codes, as embedded in the token by
        :func:`~api.routers.auth.login`.

    Raises:
        HTTPException: 401 if the header is missing, malformed, or the
            token is invalid/expired.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.removeprefix("Bearer ").strip()
    try:
        claims = verify_signed_token(token)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    try:
        user_id = int(claims["user_id"])
        company_id = int(claims["company_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is missing required claims.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return CurrentUser(
        user_id=user_id,
        company_id=company_id,
        permission_codes=frozenset(claims.get("permission_codes") or []),
    )


def require_permission(*codes: str):
    """Build a FastAPI dependency gating a route behind one or more permission codes.

    Any single match in ``codes`` grants access, mirroring
    :func:`~controllers.base_controller.requires_permission`.

    Args:
        *codes: One or more :attr:`~models.permission.Permission.code`
            values.

    Returns:
        A dependency callable returning the resolved
        :class:`CurrentUser` on success, or raising ``403`` on denial.
    """

    def dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not set(codes) & current_user.permission_codes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=_ACCESS_DENIED_MESSAGE_AR,
            )
        return current_user

    return dependency

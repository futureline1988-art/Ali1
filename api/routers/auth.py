"""POST /api/auth/login — the REST API's only unauthenticated write endpoint.

Reuses :class:`~services.auth_service.AuthService` verbatim (same
lockout policy, same audit trail as the desktop login screen), then
issues a :func:`~utils.security.create_signed_token` bearer token
carrying the user's resolved permission codes — the same claim the
desktop composition root (``main.py``) reads to decide which pages to
build, here read by :func:`api.dependencies.get_current_user` on every
subsequent request instead.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.dependencies import get_db_session
from api.schemas import LoginRequest
from config import get_config
from services.auth_service import AuthenticationError, AuthService
from utils.security import create_signed_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(
    payload: LoginRequest, session: Session = Depends(get_db_session)
) -> dict[str, Any]:
    """Authenticate a user within one company and issue a bearer token.

    Raises:
        HTTPException: 401 if the credentials are invalid, the account
            is inactive, or it is currently locked out.
    """
    service = AuthService(session, company_id=payload.company_id)
    try:
        user = service.login(payload.username, payload.password)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    permission_codes = [permission.code for permission in user.role.permissions]
    api_config = get_config().api
    token = create_signed_token(
        {
            "user_id": user.id,
            "company_id": payload.company_id,
            "permission_codes": permission_codes,
        },
        expires_in_minutes=api_config.token_expires_minutes,
    )

    user_data = user.to_dict(exclude={"password_hash"})
    user_data["role_name"] = user.role_name
    user_data["permission_codes"] = permission_codes

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in_minutes": api_config.token_expires_minutes,
        "user": user_data,
    }

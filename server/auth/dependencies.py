"""FastAPI dependency resolving the caller's identity from a bearer token.

Mirrors ``api/dependencies.py``'s ``CurrentUser``/``get_current_user``/
``require_permission`` design (one request-scoped identity, resolved
once from a bearer token, with a scope-gating dependency built on top)
generalized for a server that multiple different *applications* call —
not just one company's users — hence ``principal_type`` (which
application is calling: ``"developer_suite"``, and later
``"customer_application"``) and ``scopes`` (this server's own
authorization units, not the Attendance Client's
:class:`~models.permission.Permission` codes, which are meaningless
outside that application's own database).

Not used by any route in this phase (see ``server/api/app.py`` —
``/health`` and ``/version`` are both unauthenticated); this exists so
the first phase that adds an authenticated business endpoint has a
ready, already-tested dependency to depend on instead of inventing one
under time pressure.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status

from server.auth.tokens import TokenError, verify_token
from server.config import ServerConfig


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """The authenticated caller, resolved once per request from their bearer token.

    Attributes:
        principal_id: A stable identifier for the caller (e.g. a
            Developer Suite installation id, or a future customer
            application's registration id) — meaningful only in
            combination with :attr:`principal_type`.
        principal_type: Which kind of caller this is (e.g.
            ``"developer_suite"``, ``"customer_application"``).
        scopes: The authorization units this token grants, this
            server's own vocabulary.
    """

    principal_id: str
    principal_type: str
    scopes: frozenset[str] = frozenset()


def _get_server_config(request: Request) -> ServerConfig:
    """Read this server's configuration from application state.

    Set once in :func:`server.api.app.create_app`, not read from
    :func:`server.config.get_server_config`'s process-wide singleton —
    keeps every dependency here testable against an app built with an
    arbitrary, injected :class:`~server.config.ServerConfig` instead of
    coupling to global state.
    """
    return request.app.state.config


def get_current_principal(
    request: Request,
    authorization: str | None = Header(default=None),
) -> AuthenticatedPrincipal:
    """Resolve and verify the caller's bearer token.

    Args:
        request: The current request, used only to reach this server's
            configuration (see :func:`_get_server_config`).
        authorization: The raw ``Authorization`` header, expected in
            ``"Bearer <token>"`` form.

    Returns:
        The authenticated caller's identity and granted scopes, as
        embedded in the token by :func:`server.auth.tokens.issue_token`.

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
    config = _get_server_config(request)
    try:
        claims = verify_token(token, config=config)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    try:
        principal_id = str(claims["principal_id"])
        principal_type = str(claims["principal_type"])
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is missing required claims.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return AuthenticatedPrincipal(
        principal_id=principal_id,
        principal_type=principal_type,
        scopes=frozenset(claims.get("scopes") or []),
    )


def require_scope(*scopes: str):
    """Build a FastAPI dependency gating a route behind one or more scopes.

    Any single match in ``scopes`` grants access, mirroring
    ``api/dependencies.py``'s :func:`~api.dependencies.require_permission`.

    Args:
        *scopes: One or more scope strings a caller's token must carry.

    Returns:
        A dependency callable returning the resolved
        :class:`AuthenticatedPrincipal` on success, or raising ``403``
        on denial.
    """

    def dependency(
        principal: AuthenticatedPrincipal = Depends(get_current_principal),
    ) -> AuthenticatedPrincipal:
        if not set(scopes) & principal.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient scope for this operation.",
            )
        return principal

    return dependency

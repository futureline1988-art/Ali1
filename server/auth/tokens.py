"""Token issuance and verification for the Attendance Server.

Thin wrappers over :func:`utils.security.create_signed_token`/
:func:`utils.security.verify_signed_token` — the same dependency-free
HMAC-SHA256 signed-token primitive the Attendance Client's REST API
(``api/dependencies.py``) already uses for its bearer tokens. Reused
directly rather than reimplemented, with one deliberate difference:
every call here requires an explicit ``config`` argument and never
falls back to :func:`config.get_config`'s signing secret, so a
Attendance Server token can never be produced or accepted with the
Attendance Client's own ``APP_SECRET_KEY`` (see ``server/config.py``'s
docstring for why the two must stay independent).
"""

from __future__ import annotations

from typing import Any

from utils.security import TokenError, create_signed_token, verify_signed_token

from server.config import ServerConfig

__all__ = ["TokenError", "issue_token", "verify_token"]


def issue_token(
    claims: dict[str, Any],
    *,
    config: ServerConfig,
    expires_in_minutes: int | None = None,
) -> str:
    """Create a signed, expiring token carrying ``claims``.

    Args:
        claims: Arbitrary JSON-serializable data to embed (e.g.
            ``{"principal_id": "...", "principal_type": "developer_suite"}``).
        config: This server's configuration; supplies the signing
            secret and the default token lifetime.
        expires_in_minutes: Overrides
            :attr:`~config.ApiConfig.token_expires_minutes`.

    Returns:
        The encoded, signed token string.
    """
    return create_signed_token(
        claims,
        secret_key=config.security.secret_key,
        expires_in_minutes=expires_in_minutes
        if expires_in_minutes is not None
        else config.api.token_expires_minutes,
    )


def verify_token(token: str, *, config: ServerConfig) -> dict[str, Any]:
    """Verify a token produced by :func:`issue_token` and decode it.

    Args:
        token: The token string to verify.
        config: This server's configuration; supplies the signing
            secret the token must have been signed with.

    Returns:
        The embedded claims, including ``"iat"`` and ``"exp"``.

    Raises:
        TokenError: If the token is malformed, its signature does not
            match, or it has expired.
    """
    return verify_signed_token(token, secret_key=config.security.secret_key)

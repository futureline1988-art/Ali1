"""Password hashing, password strength validation, signed tokens, and
in-process session management.

Four independent concerns, kept in one module because they are the
security primitives every other layer (``services/auth_service.py``,
the future FastAPI layer, the UI's login/lock-screen flow) builds on:

* :func:`hash_password` / :func:`verify_password` — bcrypt password
  storage, never plaintext, never a fast general-purpose hash.
* :func:`validate_password_strength` — reusable policy check so the
  same rule set backs both the login form and any admin "create user"
  dialog.
* :func:`create_signed_token` / :func:`verify_signed_token` — compact,
  dependency-free HMAC-SHA256 signed/expiring tokens. Unlike a
  password hash, a session/API token must be independently verifiable
  without a database round trip and without ever needing to recover a
  plaintext secret, which is exactly what HMAC signing (not
  encryption, not bcrypt) is for.
* :class:`SessionManager` — the actual "is someone logged in, and have
  they gone idle too long" state machine for this desktop process.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt

from config import get_config

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(password: str, *, rounds: int | None = None) -> str:
    """Hash a plaintext password with bcrypt.

    Args:
        password: The plaintext password to hash.
        rounds: bcrypt cost factor; defaults to
            :attr:`config.SecurityConfig.bcrypt_rounds`.

    Returns:
        A UTF-8 bcrypt hash string, safe to store in
        :attr:`models.user.User.password_hash`.
    """
    resolved_rounds = rounds if rounds is not None else get_config().security.bcrypt_rounds
    salt = bcrypt.gensalt(rounds=resolved_rounds)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash.

    Args:
        password: The plaintext password to check.
        password_hash: A hash previously produced by :func:`hash_password`.

    Returns:
        ``True`` if the password matches. Returns ``False`` — never
        raises — for a malformed or corrupted hash, so callers can
        safely verify against untrusted stored data without wrapping
        every call in a try/except.
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def validate_password_strength(
    password: str, *, minimum_length: int | None = None
) -> list[str]:
    """Check a candidate password against this application's policy.

    Args:
        password: The candidate plaintext password.
        minimum_length: Overrides
            :attr:`config.SecurityConfig.minimum_password_length`.

    Returns:
        A list of human-readable violation messages; empty if the
        password satisfies every rule.
    """
    resolved_minimum = (
        minimum_length
        if minimum_length is not None
        else get_config().security.minimum_password_length
    )
    violations: list[str] = []
    if len(password) < resolved_minimum:
        violations.append(
            f"Password must be at least {resolved_minimum} characters long."
        )
    if not any(character.isalpha() for character in password):
        violations.append("Password must contain at least one letter.")
    if not any(character.isdigit() for character in password):
        violations.append("Password must contain at least one digit.")
    return violations


# ---------------------------------------------------------------------------
# Signed, expiring tokens
# ---------------------------------------------------------------------------


class TokenError(Exception):
    """Raised when a signed token is malformed, tampered with, or expired."""


def _b64url_encode(data: bytes) -> str:
    """Encode bytes as unpadded base64url text."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    """Decode unpadded base64url text back to bytes."""
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def generate_session_token() -> str:
    """Generate a cryptographically secure random opaque token.

    Returns:
        A URL-safe random token suitable as a bare session identifier
        (as opposed to :func:`create_signed_token`, which carries
        verifiable claims).
    """
    return secrets.token_urlsafe(32)


def create_signed_token(
    claims: dict[str, Any],
    *,
    secret_key: str | None = None,
    expires_in_minutes: int | None = None,
) -> str:
    """Create an HMAC-SHA256 signed, expiring token carrying ``claims``.

    The token format is ``base64url(json_payload).base64url(signature)``
    — a minimal, dependency-free relative of JWT. It is used both for
    this desktop app's own session token and, once the FastAPI layer is
    built, as its bearer token, since both need a token that can be
    verified without a database round trip.

    Args:
        claims: Arbitrary JSON-serializable data to embed (e.g.
            ``{"user_id": 1, "company_id": 2}``). Must not use the
            reserved keys ``"iat"``/``"exp"``, which this function sets.
        secret_key: Signing secret; defaults to
            :attr:`config.SecurityConfig.secret_key`.
        expires_in_minutes: Token lifetime; defaults to
            :attr:`config.SecurityConfig.session_timeout_minutes`.

    Returns:
        The encoded, signed token string.
    """
    resolved_secret = (
        secret_key if secret_key is not None else get_config().security.secret_key
    )
    resolved_expiry = (
        expires_in_minutes
        if expires_in_minutes is not None
        else get_config().security.session_timeout_minutes
    )

    now = datetime.now(timezone.utc)
    payload = {
        **claims,
        "iat": now.isoformat(),
        "exp": (now + timedelta(minutes=resolved_expiry)).isoformat(),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    payload_b64 = _b64url_encode(payload_bytes)
    signature = hmac.new(
        resolved_secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{payload_b64}.{_b64url_encode(signature)}"


def verify_signed_token(token: str, *, secret_key: str | None = None) -> dict[str, Any]:
    """Verify a token produced by :func:`create_signed_token` and decode it.

    Args:
        token: The token string to verify.
        secret_key: Signing secret; defaults to
            :attr:`config.SecurityConfig.secret_key`. Must match the key
            the token was created with.

    Returns:
        The embedded claims, including ``"iat"`` and ``"exp"``.

    Raises:
        TokenError: If the token is malformed, its signature does not
            match (tampered, or signed with a different secret), or it
            has expired.
    """
    resolved_secret = (
        secret_key if secret_key is not None else get_config().security.secret_key
    )
    try:
        payload_b64, signature_b64 = token.split(".", 1)
    except ValueError as exc:
        raise TokenError("Malformed token.") from exc

    expected_signature = hmac.new(
        resolved_secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    try:
        actual_signature = _b64url_decode(signature_b64)
    except Exception as exc:
        raise TokenError("Malformed token signature.") from exc

    if not hmac.compare_digest(expected_signature, actual_signature):
        raise TokenError("Token signature is invalid.")

    try:
        payload: dict[str, Any] = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise TokenError("Malformed token payload.") from exc

    try:
        expires_at = datetime.fromisoformat(payload["exp"])
    except (KeyError, ValueError) as exc:
        raise TokenError("Token is missing a valid expiry claim.") from exc

    if datetime.now(timezone.utc) >= expires_at:
        raise TokenError("Token has expired.")

    return payload


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


@dataclass
class SessionInfo:
    """A snapshot of the currently active session, if any."""

    user_id: int
    company_id: int
    token: str
    started_at: datetime
    last_activity_at: datetime


class SessionManager:
    """Tracks the single active login session for this desktop process.

    Thread-safe: the UI thread typically calls :meth:`touch` on every
    user interaction while a background timer thread polls
    :attr:`is_active` to decide whether to show a lock screen, so every
    access to session state is guarded by a lock.
    """

    def __init__(self, *, timeout_minutes: int | None = None) -> None:
        """Create a session manager.

        Args:
            timeout_minutes: Idle timeout before a session is considered
                expired; defaults to
                :attr:`config.SecurityConfig.session_timeout_minutes`.
        """
        self._timeout_minutes = (
            timeout_minutes
            if timeout_minutes is not None
            else get_config().security.session_timeout_minutes
        )
        self._lock = threading.Lock()
        self._session: SessionInfo | None = None

    def start_session(self, *, user_id: int, company_id: int) -> str:
        """Start a new session, replacing any previous one.

        Args:
            user_id: The authenticated :class:`~models.user.User` id.
            company_id: The company this session is scoped to.

        Returns:
            The newly generated session token.
        """
        token = generate_session_token()
        now = datetime.now(timezone.utc)
        with self._lock:
            self._session = SessionInfo(
                user_id=user_id,
                company_id=company_id,
                token=token,
                started_at=now,
                last_activity_at=now,
            )
        return token

    def touch(self) -> None:
        """Record activity on the current session, resetting its idle timer.

        A no-op if there is no active session.
        """
        with self._lock:
            if self._session is not None:
                self._session.last_activity_at = datetime.now(timezone.utc)

    def end_session(self) -> None:
        """End the current session (logout)."""
        with self._lock:
            self._session = None

    @property
    def is_active(self) -> bool:
        """Whether there is a current session that has not gone idle."""
        with self._lock:
            if self._session is None:
                return False
            idle_for = datetime.now(timezone.utc) - self._session.last_activity_at
            return idle_for < timedelta(minutes=self._timeout_minutes)

    @property
    def current_user_id(self) -> int | None:
        """The active session's user id, or ``None`` if no session is active."""
        with self._lock:
            return self._session.user_id if self._session else None

    @property
    def current_company_id(self) -> int | None:
        """The active session's company id, or ``None`` if no session is active."""
        with self._lock:
            return self._session.company_id if self._session else None

    @property
    def current_token(self) -> str | None:
        """The active session's token, or ``None`` if no session is active."""
        with self._lock:
            return self._session.token if self._session else None

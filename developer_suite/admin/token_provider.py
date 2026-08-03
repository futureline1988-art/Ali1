"""Where :class:`~developer_suite.admin.client.AdminApiClient` gets its bearer token from.

:class:`AdminTokenProvider` is the abstraction every admin-scoped API
client depends on — never a concrete token source directly. Today
exactly one implementation exists,
:class:`ConfiguredAdminTokenProvider`, an explicitly **temporary**
Phase 10 bootstrap: it reads a ``sync:admin``-scoped token from the
``DEV_SUITE_SYNC_ADMIN_TOKEN`` environment variable once and persists
it, encrypted at rest, in this installation's own database (reusing
the exact :class:`~developer_suite.models.encrypted_types.EncryptedString`
mechanism :class:`~developer_suite.models.sync_state.SyncDeviceCredential`
already established for the sync device credential), so the plaintext
token need not be re-supplied via the environment on every subsequent
run.

No usernames, passwords, sessions, or refresh tokens exist here, and
none are added by this module — this is a single static secret, an
operator/deployment credential, standing in for a real
authentication/login flow that does not exist yet anywhere in this
platform (see ``server/api/routers/devices.py``'s docstring).

**Replacing this later**: when a real login flow exists, implement
:class:`AdminTokenProvider` with a class that returns a freshly-issued,
short-lived token from that flow's session state, and construct that
class instead of :class:`ConfiguredAdminTokenProvider` wherever this
package is wired up (today: :class:`~developer_suite.container.ServiceContainer`,
the only place that constructs an
:class:`~developer_suite.admin.client.AdminApiClient`). Nothing that
depends on :class:`AdminTokenProvider` — :class:`~developer_suite.admin.client.AdminApiClient`,
:class:`~developer_suite.services.dashboard_service.DashboardService`,
or any UI page — needs to change; that is the entire point of
depending on this abstraction instead of importing
:class:`ConfiguredAdminTokenProvider` directly.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from database.database import Database
from developer_suite.repositories.admin_token_repository import AdminBootstrapTokenRepository

_ADMIN_TOKEN_ENV_VAR = "DEV_SUITE_SYNC_ADMIN_TOKEN"


@runtime_checkable
class AdminTokenProvider(Protocol):
    """Anything that can supply the bearer token for an admin-scoped API call.

    A single method, deliberately: whatever a real login flow ends up
    looking like, "give me the current token, or ``None`` if there
    isn't one" is the only contract
    :class:`~developer_suite.admin.client.AdminApiClient` actually
    needs.
    """

    def get_token(self) -> str | None:
        """Return the current admin bearer token, or ``None`` if none is available."""
        ...


class ConfiguredAdminTokenProvider:
    """**Temporary** Phase 10 bootstrap implementation of :class:`AdminTokenProvider`.

    See this module's docstring for the full rationale and the
    replacement path.
    """

    def __init__(self, database: Database) -> None:
        """Create a provider bound to this installation's own database.

        Args:
            database: The Developer Suite's own database — where the
                bootstrap token is persisted, encrypted, once read
                from the environment.
        """
        self._database = database

    def get_token(self) -> str | None:
        """Return the stored bootstrap token, reading it from the environment on first use.

        Returns:
            The token, or ``None`` if neither a stored token nor
            ``DEV_SUITE_SYNC_ADMIN_TOKEN`` is available.
        """
        with self._database.session_scope() as session:
            record = AdminBootstrapTokenRepository(session).get()
            if record is not None:
                return record.token

        env_token = os.getenv(_ADMIN_TOKEN_ENV_VAR)
        if not env_token:
            return None

        with self._database.session_scope() as session:
            AdminBootstrapTokenRepository(session).save(env_token)
        return env_token

"""Where :class:`~developer_suite.admin.client.AdminApiClient` gets its bearer token from.

:class:`AdminTokenProvider` is the abstraction every admin-scoped API
client depends on — never a concrete token source directly. This was
the seam Phase 10 built specifically so its temporary bootstrap
mechanism could later be replaced without touching anything downstream
of it; Phase 11 is that replacement.

The concrete implementation is now
:class:`~developer_suite.admin.session_manager.AdminSessionManager`
(real login/refresh/logout against ``/api/v1/auth/*`` — see
:mod:`developer_suite.admin.session_manager`), constructed once in
:class:`~developer_suite.container.ServiceContainer`. Nothing that
depends on :class:`AdminTokenProvider` —
:class:`~developer_suite.admin.client.AdminApiClient`,
:class:`~developer_suite.services.dashboard_service.DashboardService`,
or any UI page — needed to change when the Phase 10 bootstrap provider
was replaced by that real implementation; that is the entire point of
depending on this abstraction instead of a concrete class.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AdminTokenProvider(Protocol):
    """Anything that can supply the bearer token for an admin-scoped API call.

    A single method, deliberately: whatever the current authentication
    flow looks like, "give me the current token, or ``None`` if there
    isn't one" is the only contract
    :class:`~developer_suite.admin.client.AdminApiClient` actually
    needs.
    """

    def get_token(self) -> str | None:
        """Return the current admin bearer token, or ``None`` if none is available."""
        ...

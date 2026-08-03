"""Developer Suite administration: authentication + Attendance Server monitoring.

:mod:`developer_suite.admin.client` reads, never writes, remote
operational state (registered devices, recent sync activity, server
health) over the Attendance Server's administrative APIs — Phase 10's
"Developer Dashboard & Administration" work. Those calls authenticate
via the :class:`~developer_suite.admin.token_provider.AdminTokenProvider`
abstraction, whose real implementation
(:class:`~developer_suite.admin.session_manager.AdminSessionManager`,
backed by :mod:`developer_suite.admin.auth_client`) is Phase 11's real
login/session/refresh/logout system, replacing Phase 10's temporary
bootstrap token.
"""

from __future__ import annotations

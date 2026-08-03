"""Developer Suite administration: read-only Attendance Server monitoring.

Everything here is Phase 10's "Developer Dashboard & Administration"
work — reading, never writing, remote operational state (registered
devices, recent sync activity, server health) over the Attendance
Server's read-only administrative APIs. See
:mod:`developer_suite.admin.token_provider` for the temporary
bootstrap-token mechanism these calls authenticate with, and
:mod:`developer_suite.admin.client` for the HTTP client itself.
"""

from __future__ import annotations

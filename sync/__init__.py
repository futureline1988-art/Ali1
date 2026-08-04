"""Remote configuration synchronization for the Attendance Client (Phase 13).

This package is the customer-facing counterpart to
:mod:`developer_suite.sync`: it lets one installation of the Attendance
Client *pull* configuration the Developer Suite has published for it,
over the same generic Attendance Server sync API
(:mod:`developer_suite.sync.protocol`'s docstring explains why the
protocol is replicated on each side of the HTTP boundary rather than
shared by import). Nothing in this package is imported by, or imports,
:mod:`developer_suite` — the Attendance Client's packaged executable
must never carry that unrelated dependency tree (see
:mod:`sync.protocol`'s own docstring for the full reasoning).

The Attendance Client only ever pulls in this phase — it never pushes
local changes back — and it works fully offline: every module in this
package is additive, best-effort infrastructure that the rest of the
application never depends on to function (see
:mod:`sync.scheduler`'s docstring for how retries/offline behavior are
handled).
"""

from __future__ import annotations

"""Remote software update distribution for the Attendance Client (Phase 14).

The customer-facing counterpart to
:mod:`developer_suite.services.update_manager_service`: periodically
checks the Attendance Server for an update targeted at this
installation, downloads it in the background (resumable — see
:mod:`updates.client`), verifies its checksum and digital signature
(:mod:`updates.verifier`) before ever considering it safe, and reports
progress back. Nothing here imports :mod:`developer_suite` or
``server`` (see :mod:`updates.protocol`'s docstring) — the same
replicate-not-import doctrine :mod:`sync` already established for
Phase 13, reused here rather than invented anew.

This package only ever *reads* from the Attendance Server; nothing
here ever changes what data a version represents. It reuses
:mod:`sync`'s existing device credential (see
:mod:`sync.coordinator`) and its existing background scheduler (see
:mod:`sync.scheduler`) rather than introducing a second credential
store or a second periodic job — an update check is simply one more
thing the same scheduled sync cycle does.
"""

from __future__ import annotations

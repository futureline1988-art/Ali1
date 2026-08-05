"""Optional software update distribution for the Attendance Client.

The customer-facing counterpart to
:mod:`developer_suite.services.update_manager_service`, kept as a
genuinely optional feature: this application no longer requires a
central Attendance Server for normal operation, so nothing here runs
automatically. A caller (e.g. a manual "Check for Updates" action)
asks a configured update server for an update targeted at this
installation, downloads it in the background (resumable — see
:mod:`updates.client`), verifies its checksum and digital signature
(:mod:`updates.verifier`) before ever considering it safe, and reports
progress back. Nothing here imports :mod:`developer_suite` or
``server`` directly (see :mod:`updates.protocol`'s docstring).

This package only ever *reads* from the configured update server;
nothing here ever changes what data a version represents. It uses its
own, self-contained device credential
(:mod:`repositories.update_credential_repository`) — separate from any
other application concern — which is unset (``None``) for every
installation until an administrator manually configures one; until
then, every check simply reports "not configured" rather than
fabricating a connection that does not exist.
"""

from __future__ import annotations

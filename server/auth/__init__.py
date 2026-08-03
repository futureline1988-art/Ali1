"""Attendance Server authentication interfaces.

Infrastructure only — token issuance/verification and the "who is
calling" FastAPI dependency — reused by whatever future phase adds the
first actual login/registration endpoint. No such endpoint exists yet,
and nothing currently mounted in :mod:`server.api.app` depends on
anything in this package.
"""

from __future__ import annotations

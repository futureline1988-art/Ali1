"""Attendance Server API routers.

Only ``health`` and ``version`` exist in this phase — both
unauthenticated infrastructure endpoints. Business routers (customers,
licenses, remote configuration, ...) are explicitly out of scope until
their own approved phases; when they arrive, they mount under
``/api/v1`` (reserved, unused in this phase — see ``server/api/app.py``),
leaving ``/health`` and ``/version`` at the root as plain
operational-infrastructure endpoints outside any versioned business
contract.
"""

from __future__ import annotations

"""Pure validation logic that is not yet wired into the running application.

Phase 1 foundation code (see
``docs/PLATFORM_ARCHITECTURE_GAP_ANALYSIS.md``): every function in
this package is a pure, side-effect-free predicate, deliberately built
and tested standalone without being called from
:class:`~licensing.license_service.LicenseService`, ``main.py``, or
anywhere else in the live application yet. Wiring either module into
actual license/startup behavior is later-phase work requiring its own
explicit approval, per this phase's constraint that no customer-visible
or licensing behavior may change.
"""

from __future__ import annotations

from licensing.validator.developer_mode import is_developer_mode_permitted, is_frozen
from licensing.validator.version_check import is_version_licensed, parse_version

__all__ = [
    "is_frozen",
    "is_developer_mode_permitted",
    "parse_version",
    "is_version_licensed",
]

"""Developer Suite UI layer: main window and navigation sidebar.

Deliberately self-contained — this package does not import from the
Attendance Client's ``ui`` package. Database, configuration, and
security infrastructure are genuinely shared (see
:mod:`developer_suite.config`, :mod:`developer_suite.database`); each
application's *presentation* layer is not, since the two have entirely
different navigation models (five platform modules here, versus the
Attendance Client's dozen feature pages) and keeping them independent
means a future styling/behavior change to one can never silently
affect the other.
"""

from __future__ import annotations

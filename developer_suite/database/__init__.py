"""Developer Suite's own, independent database.

Reuses :class:`database.database.Database` (the exact same
engine/session/pooling machinery the Attendance Client uses) via its
public ``engine`` property — see :mod:`developer_suite.database.bootstrap`
— rather than duplicating any connection-handling logic. What is *not*
reused is :data:`models.base.Base`: that metadata object belongs to the
Attendance Client's schema. This package's :data:`~developer_suite.database.base.Base`
is a separate SQLAlchemy declarative base with its own, currently empty,
metadata — Phase 2 defines no tables yet (see this package's parent
module docstring for why).
"""

from __future__ import annotations

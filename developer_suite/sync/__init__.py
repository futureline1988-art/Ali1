"""Generic Attendance Server synchronization client for the Developer Suite.

Everything in this package is entity-type-agnostic — see
:mod:`developer_suite.sync.coordinator`'s docstring for the layering.
The one file allowed to know about a specific business entity is
:mod:`developer_suite.sync.customer_sync`, Phase 8's proof that the
generic layers underneath it need no redesign to carry a second entity
type later.
"""

from __future__ import annotations

"""Platform Server: the single source of truth every other application talks to.

Phase 6 foundation only (see ``docs/PLATFORM_ARCHITECTURE_GAP_ANALYSIS.md``):
project structure, dependency injection, configuration, database
connection, repository layer, service layer, authentication
interfaces, API routing structure, and the health/version endpoints.
No business, customer, license, or configuration endpoints exist yet —
those, along with actual synchronization between this server and the
Attendance Client / Developer Suite, are explicitly out of scope until
their own approved phases.

Per the platform's design rules, neither the Attendance Client nor the
Developer Suite communicates with the other directly; both will
eventually talk only to this server, over HTTP, never by importing
this package directly (the reverse is also true: nothing here imports
``developer_suite`` or the Attendance Client's own modules except the
same shared, generic libraries those two already reuse - ``config``,
``database.database``, ``models.base``, ``utils.security``).

Subpackages:
    database: This server's own SQLAlchemy declarative base and
        database bootstrap - a third, independent schema alongside
        ``models.base.Base`` (Attendance Client) and
        ``developer_suite.database.base.Base`` (Developer Suite).
    repositories: Generic CRUD data access for this server's models.
    services: Business-logic layer, thin for now (nothing to serve
        yet).
    auth: Authentication interfaces (token issuance/verification,
        the "current caller" FastAPI dependency) - infrastructure
        only; no login endpoint exists yet.
    api: The FastAPI application factory and routing structure -
        mounts only ``/health`` and ``/version`` in this phase.
"""

from __future__ import annotations

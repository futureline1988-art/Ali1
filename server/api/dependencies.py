"""Shared FastAPI dependencies for the Attendance Server.

Mirrors ``api/dependencies.py``'s ``get_db_session`` (one request, one
unit of work) with one deliberate difference: the Attendance Client's
version reaches for ``database.database.session_scope``'s process-wide
singleton, since that application only ever has one database. This
server is designed to serve hundreds or thousands of companies from
whatever deployment topology a later phase chooses, so its session
dependency instead reads the request-scoped
:class:`~server.container.ServiceContainer` set on ``app.state`` by
:func:`server.api.app.create_app` — no global singleton at all.
"""

from __future__ import annotations

from typing import Iterator

from fastapi import Request
from sqlalchemy.orm import Session


def get_db_session(request: Request) -> Iterator[Session]:
    """Yield a request-scoped database session for this server's own database.

    Commits when the route handler returns normally, rolls back if it
    raises — the exact transactional shape as
    :meth:`~database.database.Database.session_scope`, which this
    simply delegates to via the container on ``app.state``.
    """
    container = request.app.state.container
    with container.database.session_scope() as session:
        yield session

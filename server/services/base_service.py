"""The base every concrete Attendance Server service will build on.

Mirrors ``developer_suite/services/base_service.py`` and, through it,
the Attendance Client's own service-layer pattern (a thin class
holding a session-scope provider, with concrete methods opening one
unit of work per operation via ``with self._session_scope() as
session:``) — establishing that same, already-proven shape here rather
than inventing a different one for this server.

No concrete service subclasses this yet: business logic (customer
registry, license metadata, remote configuration, ...) is explicitly
out of scope for Phase 6. This class itself has no methods beyond the
session-scope plumbing every future service needs.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session

from database.database import Database


class BaseService:
    """Shared foundation for every Attendance Server business service.

    Attributes:
        database: This server's own database (see
            :func:`server.database.bootstrap.build_database`) — never
            the Attendance Client's or Developer Suite's.
    """

    def __init__(self, database: Database) -> None:
        """Create a service bound to ``database``.

        Args:
            database: The database this service's future concrete
                operations will read from and write to.
        """
        self.database = database

    @contextmanager
    def _session_scope(self) -> Iterator[Session]:
        """One transactional unit of work, exactly like the other two applications' services use.

        Yields:
            An active :class:`~sqlalchemy.orm.Session`, committed on
            success and rolled back on any exception.
        """
        with self.database.session_scope() as session:
            yield session

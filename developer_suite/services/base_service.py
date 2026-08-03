"""The base every concrete Developer Suite service will build on.

Mirrors the Attendance Client's own service-layer pattern (a thin class
holding a session-scope provider, with concrete methods opening one
unit of work per operation via ``with self._session_scope() as
session:``) — establishing that same, already-proven shape here rather
than inventing a different one for this application.

No concrete service subclasses this yet: business logic (customer
CRUD, license issuance/renewal/revocation, ...) is explicitly out of
scope for Phase 2. This class itself has no methods beyond the
session-scope plumbing every future service needs.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session

from database.database import Database


class BaseService:
    """Shared foundation for every Developer Suite platform service.

    Attributes:
        database: The Developer Suite's own database (see
            :func:`developer_suite.database.bootstrap.build_database`) —
            never the Attendance Client's.
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
        """One transactional unit of work, exactly like the Attendance Client's services use.

        Yields:
            An active :class:`~sqlalchemy.orm.Session`, committed on
            success and rolled back on any exception.
        """
        with self.database.session_scope() as session:
            yield session

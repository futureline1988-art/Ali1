"""Synchronization status: a generic, entity-agnostic health signal.

Reported by :class:`~developer_suite.sync.scheduler.SyncSchedulerService`
so the rest of the application (today: nothing; eventually, a status
indicator in the UI) can answer "is synchronization working right
now?" without knowing anything about how push/pull actually happen —
that stays entirely inside :mod:`developer_suite.sync.coordinator`.
Nothing here has ever heard of a customer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class SyncState(str, Enum):
    """The background synchronization job's current condition."""

    IDLE = "idle"
    SYNCHRONIZING = "synchronizing"
    OFFLINE = "offline"
    ERROR = "error"


@dataclass(frozen=True)
class SyncStatus:
    """A point-in-time snapshot of the background synchronization job's health.

    Attributes:
        state: The current condition (see :class:`SyncState`).
        last_success_at: When a sync cycle last completed with every
            step succeeding, or ``None`` if it never has.
        last_failure_at: When a sync cycle last ended in
            :attr:`~SyncState.OFFLINE` or :attr:`~SyncState.ERROR`, or
            ``None`` if it never has.
        last_error_message: The most recent failure's message; cleared
            on the next success.
        pending_changes_count: How many local changes are currently
            queued, waiting to be pushed — always a live count from
            the local outbox, independent of when the last cycle ran
            (see
            :meth:`~developer_suite.sync.scheduler.SyncSchedulerService.get_status`).
        consecutive_failures: How many sync cycles in a row have ended
            in :attr:`~SyncState.OFFLINE`/:attr:`~SyncState.ERROR`;
            reset to ``0`` on the next success.
    """

    state: SyncState = SyncState.IDLE
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error_message: str | None = None
    pending_changes_count: int = 0
    consecutive_failures: int = 0

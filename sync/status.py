"""Synchronization status: a generic, entity-agnostic health signal.

Mirrors :mod:`developer_suite.sync.status` exactly, minus
``pending_changes_count`` — this installation has no outbox (it never
pushes local changes in this phase; see :mod:`sync` package
docstring), so there is nothing local queued to report a count for.
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
        last_success_at: When a sync cycle last completed successfully,
            or ``None`` if it never has.
        last_failure_at: When a sync cycle last ended in
            :attr:`~SyncState.OFFLINE` or :attr:`~SyncState.ERROR`, or
            ``None`` if it never has.
        last_error_message: The most recent failure's message; cleared
            on the next success.
        consecutive_failures: How many sync cycles in a row have ended
            in failure; reset to ``0`` on the next success.
        restart_required: Whether the most recently applied
            configuration requires this application to restart to
            take full effect (mirrors
            :attr:`~models.company_settings.CompanySettings.remote_config_restart_required`).
    """

    state: SyncState = SyncState.IDLE
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error_message: str | None = None
    consecutive_failures: int = 0
    restart_required: bool = False

"""Automatic, periodic remote-configuration synchronization for the Attendance Client.

Runs on :class:`apscheduler.schedulers.background.BackgroundScheduler`,
the same engine :mod:`services.scheduler_service` already uses for
this application's device-sync/backup jobs, and
:mod:`developer_suite.sync.scheduler` uses for the Developer Suite's
own background sync — reused here unmodified as a pattern, not by
import (see :mod:`sync` package docstring for why nothing in this
package imports :mod:`developer_suite`).

This is what satisfies "retry synchronization automatically" and
"continue working offline": a not-yet-enrolled or currently
unreachable Attendance Server never blocks or crashes anything —
:meth:`ClientSyncSchedulerService.sync_now` swallows every failure into
:attr:`~sync.status.SyncState.OFFLINE`/:attr:`~sync.status.SyncState.ERROR`
and the *next* scheduled tick, on its own normal cadence, is what
resumes synchronization once connectivity returns; no part of the rest
of the application ever waits on this scheduler.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from database.database import Database
from sync.client import SyncConnectionError
from sync.configuration_apply import ENTITY_TYPE
from sync.coordinator import ClientSyncCoordinator
from sync.status import SyncState, SyncStatus
from utils.logger import logger

_SYNC_JOB_ID = "attendance_client_remote_configuration_sync"
_DEFAULT_MAX_RETRIES_PER_CYCLE = 3
_DEFAULT_RETRY_BACKOFF_SECONDS = 2.0


class ClientSyncSchedulerService:
    """Owns the process-wide background remote-configuration pull job.

    One instance is created and driven by ``main.py``: :meth:`start`
    once after the rest of startup has succeeded, :meth:`shutdown`
    once before the application exits — the same lifecycle
    :class:`~services.scheduler_service.SchedulerService` already
    establishes for this application's other background jobs.
    """

    def __init__(
        self,
        coordinator: ClientSyncCoordinator,
        database: Database,
        *,
        sync_enabled: bool,
        sync_interval_seconds: int,
        max_retries_per_cycle: int = _DEFAULT_MAX_RETRIES_PER_CYCLE,
        retry_backoff_seconds: float = _DEFAULT_RETRY_BACKOFF_SECONDS,
    ) -> None:
        """Create a scheduler service (does not start any job yet).

        Args:
            coordinator: The pull-and-apply engine this service
                schedules calls against.
            database: Used only to read
                :attr:`~models.company_settings.CompanySettings.remote_config_restart_required`
                for :meth:`get_status`.
            sync_enabled: Whether to actually register the periodic
                job in :meth:`start` (mirrors
                :attr:`~developer_suite.config.DeveloperSuiteConfig.sync_enabled`).
            sync_interval_seconds: Seconds between automatic pull
                cycles.
            max_retries_per_cycle: How many attempts one pull gets
                before a connection failure ends the cycle as
                :attr:`~sync.status.SyncState.OFFLINE`.
            retry_backoff_seconds: Base delay between retries within
                one cycle, multiplied by the attempt number.
        """
        self._coordinator = coordinator
        self._database = database
        self._sync_enabled = sync_enabled
        self._sync_interval_seconds = sync_interval_seconds
        self._max_retries_per_cycle = max_retries_per_cycle
        self._retry_backoff_seconds = retry_backoff_seconds
        self._scheduler = BackgroundScheduler(daemon=True)
        self._status_lock = threading.Lock()
        self._cycle_lock = threading.Lock()
        self._status = SyncStatus()

    def start(self) -> None:
        """Register and start the periodic pull job, if enabled by configuration.

        A no-op if ``sync_enabled`` was ``False`` at construction —
        the Attendance Client keeps working fully offline either way.
        """
        if not self._sync_enabled:
            logger.info("Remote configuration sync is disabled by configuration.")
            return

        self._scheduler.add_job(
            self.sync_now,
            "interval",
            seconds=max(1, self._sync_interval_seconds),
            id=_SYNC_JOB_ID,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self._scheduler.start()
        logger.info(
            "Scheduled remote configuration sync every {seconds} second(s).",
            seconds=self._sync_interval_seconds,
        )

    def shutdown(self) -> None:
        """Stop the scheduler, without waiting for an in-flight cycle to finish."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def get_status(self) -> SyncStatus:
        """Return a snapshot of the current synchronization status."""
        with self._status_lock:
            snapshot = self._status
        return replace(snapshot, restart_required=self._restart_required())

    def sync_now(self) -> SyncStatus:
        """Run one pull cycle immediately, outside the schedule.

        Safe to call concurrently with a scheduled tick or another
        manual call — a call that finds a cycle already running
        returns the current status immediately without starting a
        second one.
        """
        if not self._cycle_lock.acquire(blocking=False):
            return self.get_status()
        try:
            self._run_cycle()
        finally:
            self._cycle_lock.release()
        return self.get_status()

    def _run_cycle(self) -> None:
        if not self._coordinator.is_enrolled():
            self._set_state(SyncState.IDLE)
            return

        self._set_state(SyncState.SYNCHRONIZING)
        try:
            self._with_retries(lambda: self._coordinator.pull_and_apply(ENTITY_TYPE))
        except SyncConnectionError as exc:
            self._record_failure(SyncState.OFFLINE, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - a scheduled job must never crash the app
            self._record_failure(SyncState.ERROR, str(exc))
            return

        self._record_success()

    def _with_retries(self, operation) -> None:
        """Call ``operation``, retrying only on a transient connection failure."""
        attempt = 1
        while True:
            try:
                operation()
                return
            except SyncConnectionError:
                if attempt >= self._max_retries_per_cycle:
                    raise
                time.sleep(self._retry_backoff_seconds * attempt)
                attempt += 1

    def _restart_required(self) -> bool:
        from sqlalchemy import select

        from models.company_settings import CompanySettings

        with self._database.session_scope() as session:
            row = session.execute(
                select(CompanySettings.remote_config_restart_required).where(
                    CompanySettings.remote_config_restart_required.is_(True)
                )
            ).scalars().first()
            return bool(row)

    def _set_state(self, state: SyncState) -> None:
        with self._status_lock:
            self._status = replace(self._status, state=state)

    def _record_success(self) -> None:
        with self._status_lock:
            self._status = SyncStatus(
                state=SyncState.IDLE,
                last_success_at=datetime.now(timezone.utc),
                last_failure_at=self._status.last_failure_at,
                last_error_message=None,
                consecutive_failures=0,
            )

    def _record_failure(self, state: SyncState, message: str) -> None:
        with self._status_lock:
            self._status = SyncStatus(
                state=state,
                last_success_at=self._status.last_success_at,
                last_failure_at=datetime.now(timezone.utc),
                last_error_message=message,
                consecutive_failures=self._status.consecutive_failures + 1,
            )
        logger.warning("Remote configuration sync failed: {error}", error=message)

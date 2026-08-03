"""Automatic, periodic synchronization for the Developer Suite.

Runs on :class:`apscheduler.schedulers.background.BackgroundScheduler`,
the same engine and configuration shape
:mod:`services.scheduler_service` already established for the
Attendance Client's own device-sync/backup jobs (see that module's
docstring for why ``BackgroundScheduler`` — worker threads, never the
Qt event loop — is the right choice for a desktop app). This module
owns only *when* to synchronize; *how* is entirely
:class:`~developer_suite.sync.coordinator.SyncCoordinator`'s job,
reused here completely unmodified — no push/pull/conflict logic is
duplicated, and no business entity (Customer today, anything else
later) is ever named in this file. Every currently-synced entity type
is reached uniformly through
:meth:`~developer_suite.sync.coordinator.SyncCoordinator.registered_entity_types`.

One sync cycle, on every scheduled tick or manual :meth:`SyncSchedulerService.sync_now`
call:

1. If this installation has never enrolled (see
   :meth:`~developer_suite.sync.coordinator.SyncCoordinator.is_enrolled`),
   the cycle is a no-op and status stays/returns to
   :attr:`~developer_suite.sync.status.SyncState.IDLE` — Phase 9 does
   not add an enrollment flow (see ``server/api/routers/devices.py``'s
   docstring for why none exists yet), so a fresh, not-yet-enrolled
   installation is an expected, non-error steady state, not something
   to report as broken.
2. Otherwise: push every pending local change, then pull every
   registered entity type. Both steps go through :meth:`_with_retries`,
   which retries only :class:`~developer_suite.sync.client.SyncConnectionError`
   (a transient network failure) a bounded number of times with a short
   backoff before giving up for this cycle — smoothing over blips
   shorter than the polling interval without turning a genuine, sustained
   outage into a long-blocking retry loop. A sustained outage instead
   surfaces as :attr:`~developer_suite.sync.status.SyncState.OFFLINE`;
   the *next* scheduled tick, on its own normal cadence, is what
   "resumes automatically when connectivity returns" — no special
   reconnection logic is needed beyond simply trying again.
3. Any other failure (auth rejected, unexpected server response, a
   local bug) surfaces as :attr:`~developer_suite.sync.status.SyncState.ERROR`
   instead — never retried within the same cycle, since retrying
   immediately cannot fix a rejected credential or a server-side
   error.

Concurrency: at most one cycle ever runs at a time (a re-entrant
scheduled tick, or a manual :meth:`SyncSchedulerService.sync_now` call
overlapping one already in flight, returns the current status
immediately instead of starting a second cycle) — both APScheduler's
own ``max_instances=1`` for the scheduled path, and an explicit
non-blocking lock covering the manual path too, since a test or a
future UI action can call :meth:`SyncSchedulerService.sync_now`
directly without going through the scheduler at all.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler

from developer_suite.config import DeveloperSuiteConfig
from developer_suite.sync.client import SyncConnectionError
from developer_suite.sync.coordinator import SyncCoordinator
from developer_suite.sync.status import SyncState, SyncStatus
from utils.logger import logger

_SYNC_JOB_ID = "developer_suite_background_sync"
_DEFAULT_MAX_RETRIES_PER_CYCLE = 3
_DEFAULT_RETRY_BACKOFF_SECONDS = 2.0


class SyncSchedulerService:
    """Owns the process-wide background synchronization job.

    One instance is created by the composition root
    (:class:`~developer_suite.container.ServiceContainer`) and lives
    for the process's lifetime; :meth:`start` is called once after the
    container is ready, :meth:`shutdown` once before the application
    exits — mirroring exactly how :class:`~services.scheduler_service.SchedulerService`
    is driven from the Attendance Client's own ``main.py``.
    """

    def __init__(
        self,
        coordinator: SyncCoordinator,
        config: DeveloperSuiteConfig,
        *,
        max_retries_per_cycle: int = _DEFAULT_MAX_RETRIES_PER_CYCLE,
        retry_backoff_seconds: float = _DEFAULT_RETRY_BACKOFF_SECONDS,
    ) -> None:
        """Create a scheduler service (does not start any job yet).

        Args:
            coordinator: The push/pull engine this service schedules
                calls against.
            config: This application's configuration; supplies
                :attr:`~developer_suite.config.DeveloperSuiteConfig.sync_enabled`
                and
                :attr:`~developer_suite.config.DeveloperSuiteConfig.sync_interval_seconds`.
            max_retries_per_cycle: How many attempts one push or pull
                step gets before a connection failure ends the cycle
                as :attr:`~developer_suite.sync.status.SyncState.OFFLINE`.
                Overridable for tests; production code should leave
                the default.
            retry_backoff_seconds: Base delay between retries within
                one cycle, multiplied by the attempt number. Overridable
                for tests, for the same reason.
        """
        self._coordinator = coordinator
        self._config = config
        self._max_retries_per_cycle = max_retries_per_cycle
        self._retry_backoff_seconds = retry_backoff_seconds
        self._scheduler = BackgroundScheduler(daemon=True)
        self._status_lock = threading.Lock()
        self._cycle_lock = threading.Lock()
        self._status = SyncStatus()

    def start(self) -> None:
        """Register and start the periodic sync job, if enabled by configuration.

        A no-op if :attr:`~developer_suite.config.DeveloperSuiteConfig.sync_enabled`
        is ``False`` — the same "governing config disables it" shape
        :class:`~services.scheduler_service.SchedulerService.start`
        already uses for device sync.
        """
        if not self._config.sync_enabled:
            logger.info("Developer Suite background sync is disabled by configuration.")
            return

        self._scheduler.add_job(
            self.sync_now,
            "interval",
            seconds=max(1, self._config.sync_interval_seconds),
            id=_SYNC_JOB_ID,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self._scheduler.start()
        logger.info(
            "Scheduled background sync every {seconds} second(s).",
            seconds=self._config.sync_interval_seconds,
        )

    def shutdown(self) -> None:
        """Stop the scheduler, without waiting for an in-flight cycle to finish."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def get_status(self) -> SyncStatus:
        """Return a snapshot of the current synchronization status.

        ``pending_changes_count`` is always recomputed live from the
        local outbox at call time, independent of when the last cycle
        ran or finished.
        """
        with self._status_lock:
            snapshot = self._status
        return replace(snapshot, pending_changes_count=self._coordinator.count_pending())

    def sync_now(self) -> SyncStatus:
        """Run one synchronization cycle immediately, outside the schedule.

        Safe to call concurrently with a scheduled tick or another
        manual call — see this module's docstring on concurrency. A
        call that finds a cycle already running returns the current
        status immediately without starting a second one.
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
            self._with_retries(self._coordinator.push_pending)
            for entity_type in self._coordinator.registered_entity_types():
                self._with_retries(self._pull_one(entity_type))
        except SyncConnectionError as exc:
            self._record_failure(SyncState.OFFLINE, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - a scheduled job must never crash the app
            self._record_failure(SyncState.ERROR, str(exc))
            return

        self._record_success()

    def _pull_one(self, entity_type: str) -> Callable[[], None]:
        def _call() -> None:
            self._coordinator.pull_and_apply(entity_type)

        return _call

    def _with_retries(self, operation: Callable[[], object]) -> None:
        """Call ``operation``, retrying only on a transient connection failure.

        Args:
            operation: A zero-argument callable to invoke.

        Raises:
            SyncConnectionError: Every attempt, including retries,
                failed to reach the server.
            Exception: Any other exception ``operation`` raises,
                propagated immediately on the first attempt (not a
                connection problem, so retrying cannot help).
        """
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
        logger.warning("Developer Suite background sync failed: {error}", error=message)

"""Background, non-blocking refresh for the Developer Dashboard.

Computing a :class:`~developer_suite.services.dashboard_service.DashboardSnapshot`
touches the local database *and* makes several HTTP calls to the
Attendance Server (see :class:`~developer_suite.admin.client.AdminApiClient`)
— exactly the kind of work that must never run on Qt's UI thread, per
Phase 12's explicit "never block the UI" requirement. This module owns
*when* to refresh and *how* to hand that work to a background thread;
it adds no aggregation logic of its own —
:class:`~developer_suite.services.dashboard_service.DashboardService`
remains the only place a snapshot is actually computed.

Mirrors the shape :class:`~developer_suite.sync.scheduler.SyncSchedulerService`
already established for periodic background work, but on Qt's own
threading primitives (:class:`~PySide6.QtCore.QThread`/:class:`~PySide6.QtCore.QTimer`)
rather than APScheduler, since this worker's result must be delivered
back to Qt widgets living on the UI thread — a queued signal
connection is the safe way to cross that boundary, not a raw Python
callback from an arbitrary thread.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from developer_suite.services.dashboard_service import DashboardService, DashboardSnapshot
from utils.logger import logger

#: How often the dashboard refreshes itself with no user interaction.
#: Deliberately more frequent than :class:`~developer_suite.sync.scheduler.SyncSchedulerService`'s
#: own default sync interval isn't required here — this only redraws
#: already-synchronized state, it doesn't drive synchronization itself.
_DEFAULT_REFRESH_INTERVAL_MS = 15_000


class _SnapshotWorker(QObject):
    """Runs one :meth:`~developer_suite.services.dashboard_service.DashboardService.get_snapshot`
    call on whatever thread it has been moved to.

    A private implementation detail of :class:`DashboardRefreshService`
    — never constructed directly by a caller.
    """

    finished = Signal(object)
    """Emitted with the computed :class:`~developer_suite.services.dashboard_service.DashboardSnapshot`."""

    failed = Signal(str)
    """Emitted with a human-readable message if the snapshot could not be computed at all."""

    def __init__(self, dashboard_service: DashboardService) -> None:
        super().__init__()
        self._dashboard_service = dashboard_service

    def run(self) -> None:
        """Compute one snapshot and emit the result.

        :meth:`~developer_suite.services.dashboard_service.DashboardService.get_snapshot`
        already degrades individual remote-data fields to ``None``
        rather than raising (see that method's own docstring); this
        only guards against something unrelated going wrong (e.g. the
        local database itself being unreachable), so a background
        refresh failure never crashes the application or leaves the
        dashboard silently stuck.
        """
        try:
            snapshot = self._dashboard_service.get_snapshot()
        except Exception as exc:  # noqa: BLE001 - a background worker must never crash the app
            self.failed.emit(str(exc))
            return
        self.finished.emit(snapshot)


class DashboardRefreshService(QObject):
    """Owns the periodic, non-blocking dashboard refresh.

    One instance is created by the composition root
    (:class:`~developer_suite.container.ServiceContainer`) and shared
    by every widget that needs live platform data — the Dashboard page
    and :class:`~developer_suite.ui.main_window.MainWindow`'s status
    bar both connect to :attr:`snapshot_ready` rather than each polling
    the server independently, so the Attendance Server only ever sees
    one set of status/device/audit-log requests per refresh tick, not
    one per interested widget.

    Attributes:
        snapshot_ready: Emitted on the Qt UI thread with a freshly
            computed :class:`~developer_suite.services.dashboard_service.DashboardSnapshot`,
            whenever a refresh (scheduled or manually triggered via
            :meth:`refresh_now`) completes successfully.
    """

    snapshot_ready = Signal(object)

    def __init__(
        self,
        dashboard_service: DashboardService,
        *,
        interval_ms: int = _DEFAULT_REFRESH_INTERVAL_MS,
        parent: QObject | None = None,
    ) -> None:
        """Create a refresh service bound to ``dashboard_service`` (does not start ticking yet).

        Args:
            dashboard_service: Computes each snapshot.
            interval_ms: How often to refresh automatically, in
                milliseconds.
            parent: Optional Qt parent, for automatic cleanup.
        """
        super().__init__(parent)
        self._dashboard_service = dashboard_service
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.refresh_now)
        self._thread: QThread | None = None
        self._worker: _SnapshotWorker | None = None
        self._refresh_in_flight = False

    def start(self) -> None:
        """Start the periodic timer and immediately trigger a first refresh."""
        self._timer.start()
        self.refresh_now()

    def stop(self) -> None:
        """Stop the periodic timer and wait for any in-flight refresh to finish."""
        self._timer.stop()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
            self._thread = None
            self._worker = None

    def refresh_now(self) -> None:
        """Trigger one refresh immediately, on a background thread.

        A no-op if a refresh is already in flight — the same
        "overlapping calls collapse into the one already running"
        discipline :meth:`~developer_suite.sync.scheduler.SyncSchedulerService.sync_now`
        already established, so a slow HTTP round-trip can never cause
        refresh requests to pile up faster than they complete.
        """
        if self._refresh_in_flight:
            return
        self._refresh_in_flight = True

        thread = QThread(self)
        worker = _SnapshotWorker(self._dashboard_service)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        # Keep references alive for the duration of the run — nothing
        # else holds them, and a garbage-collected QThread/QObject
        # would be destroyed out from under the still-running thread.
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_finished(self, snapshot: DashboardSnapshot) -> None:
        # Drop our references before thread.quit()/deleteLater() (both
        # connected to the same worker.finished/failed signals, after
        # this slot) actually tear the QThread down — see refresh_now()'s
        # connection order. Otherwise a later stop() call could touch
        # an already-deleted QThread.
        self._thread = None
        self._worker = None
        self._refresh_in_flight = False
        self.snapshot_ready.emit(snapshot)

    def _on_failed(self, message: str) -> None:
        self._thread = None
        self._worker = None
        self._refresh_in_flight = False
        logger.warning("Developer Suite dashboard refresh failed: {error}", error=message)

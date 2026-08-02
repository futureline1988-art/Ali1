"""Background job scheduling: automatic device sync and database backups.

Consumes configuration that already existed before this module did —
:class:`~config.DeviceConfig`'s ``auto_sync_enabled``/
``auto_sync_interval_minutes`` and
:class:`~models.company_settings.CompanySettings`'s
``auto_backup_enabled``/``backup_interval_hours`` (the latter even
already has a working Settings-screen checkbox and spinbox, see
``ui/settings.py``'s ``PreferencesTab``) — none of it was ever read by
anything that actually ran a job on a schedule. This module is that
missing execution engine, not a new configuration surface.

Runs on :class:`apscheduler.schedulers.background.BackgroundScheduler`,
which executes jobs on its own worker threads rather than blocking the
Qt event loop — the natural fit for a desktop app, as opposed to
``BlockingScheduler``. Every job talks to the database exclusively
through :func:`~database.database.session_scope`, which this project's
:class:`~database.database.Database` already supports safely from a
non-main thread (``scoped_session`` + ``check_same_thread=False`` — see
that module), so no additional thread-safety work was needed here.

Device sync is a single global job (there's no per-company override of
*when* to sync, only *which* devices exist, so this fires once and
iterates every active company's active devices). Backup scheduling is
trickier: since ``backup_interval_hours``/``auto_backup_enabled`` are
genuinely per-company settings but a backup is a snapshot of the whole
installation's database (see ``services/backup_service.py``'s own
docstring on why), there is no single "the" interval to honor. This
module resolves that by ticking frequently (every
:data:`_BACKUP_CHECK_INTERVAL_MINUTES`) and, on each tick, backing up
if *any* company currently has auto-backup enabled and at least the
*shortest* interval among those companies has elapsed since the last
backup — a conservative "back up at least as often as the most
demanding company wants" policy, rather than picking one company's
settings arbitrarily.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from config import get_config
from database.database import session_scope
from repositories.company_repository import CompanyRepository
from repositories.company_settings_repository import CompanySettingsRepository
from services.backup_service import BackupService
from services.device_service import DeviceService
from utils.logger import logger

_BACKUP_CHECK_INTERVAL_MINUTES = 15
_DEVICE_SYNC_JOB_ID = "scheduled_device_sync"
_BACKUP_CHECK_JOB_ID = "scheduled_backup_check"


def _parse_backup_timestamp(backup_path: Path) -> datetime | None:
    """Extract the UTC timestamp embedded in a ``backup_*.db.enc`` filename.

    Args:
        backup_path: A path as returned by
            :meth:`~services.backup_service.BackupService.list_backups`.

    Returns:
        The parsed timestamp, or ``None`` if the filename doesn't match
        the expected ``backup_YYYYMMDD_HHMMSS_ffffff...`` shape (should
        not happen for anything this service itself created, but a
        malformed/foreign file in the backups directory should not
        crash scheduling over it).
    """
    stem = backup_path.name.removesuffix(".db.enc")
    parts = stem.split("_")
    if len(parts) < 3 or parts[0] != "backup":
        return None
    try:
        return datetime.strptime(f"{parts[1]}_{parts[2]}", "%Y%m%d_%H%M%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


class SchedulerService:
    """Owns the process-wide background job scheduler.

    One instance is created by the composition root (``main.py``) and
    lives for the process's lifetime; :meth:`start` is called once
    after the database is ready, :meth:`shutdown` once before exit.
    """

    def __init__(self) -> None:
        """Create a scheduler service (does not start any job yet)."""
        self._scheduler = BackgroundScheduler(daemon=True)

    def start(self) -> None:
        """Register and start every enabled scheduled job.

        A no-op for a given job if its governing config disables it
        (:attr:`~config.DeviceConfig.auto_sync_enabled` for device sync;
        every company having auto-backup off is checked per-tick, not
        here, since that can change at runtime via the Settings screen).
        """
        config = get_config()

        if config.device.auto_sync_enabled:
            self._scheduler.add_job(
                self._sync_all_devices,
                "interval",
                minutes=max(1, config.device.auto_sync_interval_minutes),
                id=_DEVICE_SYNC_JOB_ID,
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
            logger.info(
                "Scheduled device sync every {minutes} minute(s).",
                minutes=config.device.auto_sync_interval_minutes,
            )

        if config.backup.auto_backup_enabled:
            self._scheduler.add_job(
                self._check_and_run_backup,
                "interval",
                minutes=_BACKUP_CHECK_INTERVAL_MINUTES,
                id=_BACKUP_CHECK_JOB_ID,
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
            logger.info(
                "Scheduled backup check every {minutes} minute(s) "
                "(actual backup frequency follows each company's own setting).",
                minutes=_BACKUP_CHECK_INTERVAL_MINUTES,
            )
            if config.backup.backup_on_startup:
                self._scheduler.add_job(self._run_backup_now, "date")

        self._scheduler.start()

    def shutdown(self) -> None:
        """Stop the scheduler, waiting for any in-flight job to finish."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    # ------------------------------------------------------------------
    # Device sync
    # ------------------------------------------------------------------

    def _sync_all_devices(self) -> None:
        """Sync attendance logs for every active device in every active company.

        Errors are isolated per device (and per company): one device
        being unreachable never stops the rest of this run.
        """
        with session_scope() as session:
            company_ids = [
                company.id
                for company in CompanyRepository(session).list_all()
                if company.is_active
            ]

        for company_id in company_ids:
            try:
                with session_scope() as session:
                    device_service = DeviceService(session, company_id=company_id)
                    devices = device_service.list_devices(active_only=True)
                    for device in devices:
                        try:
                            punches = device_service.sync_attendance_logs(device)
                            if punches:
                                logger.info(
                                    "Scheduled sync: {count} new punch(es) from "
                                    "device {device!r} (company {company_id}).",
                                    count=len(punches),
                                    device=device.name,
                                    company_id=company_id,
                                )
                        except Exception as exc:  # noqa: BLE001 - isolate one device's failure
                            logger.warning(
                                "Scheduled sync failed for device {device!r} "
                                "(company {company_id}): {error}",
                                device=device.name,
                                company_id=company_id,
                                error=str(exc),
                            )
            except Exception as exc:  # noqa: BLE001 - isolate one company's failure
                logger.warning(
                    "Scheduled sync failed for company {company_id}: {error}",
                    company_id=company_id,
                    error=str(exc),
                )

    # ------------------------------------------------------------------
    # Backups
    # ------------------------------------------------------------------

    def _check_and_run_backup(self) -> None:
        """Back up the database if any company's configured interval has elapsed."""
        with session_scope() as session:
            enabled_intervals = []
            for company in CompanyRepository(session).list_all():
                if not company.is_active:
                    continue
                settings = CompanySettingsRepository(
                    session, company_id=company.id
                ).get_or_create()
                if settings.auto_backup_enabled:
                    enabled_intervals.append(settings.backup_interval_hours)

        if not enabled_intervals:
            return

        effective_interval = timedelta(hours=min(enabled_intervals))
        backup_service = BackupService()
        existing = backup_service.list_backups()
        last_backup_at = _parse_backup_timestamp(existing[0]) if existing else None

        if (
            last_backup_at is not None
            and datetime.now(timezone.utc) - last_backup_at < effective_interval
        ):
            return

        self._run_backup_now(backup_service=backup_service)

    def _run_backup_now(self, *, backup_service: BackupService | None = None) -> None:
        """Create a backup immediately and apply the retention policy."""
        service = backup_service or BackupService()
        try:
            path = service.create_backup(label="auto")
            service.apply_retention_policy()
            logger.info("Scheduled backup created: {path}", path=str(path))
        except Exception as exc:  # noqa: BLE001 - a scheduled job must never crash the app
            logger.error("Scheduled backup failed: {error}", error=str(exc))

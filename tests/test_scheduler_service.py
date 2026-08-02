"""SchedulerService: the pure decision logic behind scheduled device sync and backups.

Tests the private job methods directly (``_check_and_run_backup``,
``_sync_all_devices``) rather than waiting on real APScheduler
intervals, and :func:`~services.scheduler_service._parse_backup_timestamp`
as a standalone unit — the same approach used to verify this module
when it was first built.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

from database.database import session_scope
from repositories.company_settings_repository import CompanySettingsRepository
from services.scheduler_service import SchedulerService, _parse_backup_timestamp


def test_parse_backup_timestamp_valid_filename():
    path = Path("backup_20260215_093045_123456.db.enc")
    parsed = _parse_backup_timestamp(path)
    assert parsed == datetime(2026, 2, 15, 9, 30, 45, tzinfo=timezone.utc)


def test_parse_backup_timestamp_malformed_filename_returns_none():
    assert _parse_backup_timestamp(Path("not_a_backup_file.db.enc")) is None
    assert _parse_backup_timestamp(Path("backup_garbage.db.enc")) is None


def test_check_and_run_backup_skips_when_no_company_has_it_enabled(company_factory):
    company_id = company_factory()
    with session_scope() as session:
        settings = CompanySettingsRepository(session, company_id=company_id).get_or_create()
        settings.auto_backup_enabled = False

    scheduler = SchedulerService()
    scheduler._run_backup_now = MagicMock()
    scheduler._check_and_run_backup()
    scheduler._run_backup_now.assert_not_called()


def test_check_and_run_backup_runs_when_enabled_and_no_prior_backup(company_factory):
    company_id = company_factory()
    with session_scope() as session:
        settings = CompanySettingsRepository(session, company_id=company_id).get_or_create()
        settings.auto_backup_enabled = True
        settings.backup_interval_hours = 24

    scheduler = SchedulerService()
    scheduler._run_backup_now = MagicMock()
    scheduler._check_and_run_backup()
    scheduler._run_backup_now.assert_called_once()


def test_check_and_run_backup_skips_when_interval_not_elapsed(company_factory, monkeypatch):
    company_id = company_factory()
    with session_scope() as session:
        settings = CompanySettingsRepository(session, company_id=company_id).get_or_create()
        settings.auto_backup_enabled = True
        settings.backup_interval_hours = 24

    recent_backup = Path(
        f"backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_000001.db.enc"
    )
    scheduler = SchedulerService()
    scheduler._run_backup_now = MagicMock()

    fake_backup_service = MagicMock()
    fake_backup_service.list_backups.return_value = [recent_backup]
    monkeypatch.setattr(
        "services.scheduler_service.BackupService", lambda: fake_backup_service
    )

    scheduler._check_and_run_backup()
    scheduler._run_backup_now.assert_not_called()


def test_check_and_run_backup_runs_when_interval_elapsed(company_factory, monkeypatch):
    company_id = company_factory()
    with session_scope() as session:
        settings = CompanySettingsRepository(session, company_id=company_id).get_or_create()
        settings.auto_backup_enabled = True
        settings.backup_interval_hours = 1

    stale_time = datetime.now(timezone.utc) - timedelta(hours=5)
    stale_backup = Path(f"backup_{stale_time.strftime('%Y%m%d_%H%M%S')}_000001.db.enc")

    scheduler = SchedulerService()
    scheduler._run_backup_now = MagicMock()

    fake_backup_service = MagicMock()
    fake_backup_service.list_backups.return_value = [stale_backup]
    monkeypatch.setattr(
        "services.scheduler_service.BackupService", lambda: fake_backup_service
    )

    scheduler._check_and_run_backup()
    scheduler._run_backup_now.assert_called_once()


def test_sync_all_devices_isolates_one_devices_failure(company_factory, monkeypatch):
    """One device raising must not stop the rest of the sync run or the process."""
    company_id = company_factory()

    from services.device_service import DeviceService
    from models.enums import DeviceProtocol

    with session_scope() as session:
        service = DeviceService(session, company_id=company_id)
        service.create_device(
            name="جهاز 1", protocol=DeviceProtocol.ZKTECO_TCP, host="10.0.0.1", port=4370
        )
        service.create_device(
            name="جهاز 2", protocol=DeviceProtocol.ZKTECO_TCP, host="10.0.0.2", port=4370
        )

    def fake_sync(self, device):
        if device.name == "جهاز 1":
            raise ConnectionError("simulated device failure")
        return []

    monkeypatch.setattr(
        "services.device_service.DeviceService.sync_attendance_logs", fake_sync
    )

    scheduler = SchedulerService()
    # Must not raise, even though one device's sync raises internally.
    scheduler._sync_all_devices()

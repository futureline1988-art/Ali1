"""Tests for Phase 12: the real Developer Dashboard.

Groups:

* Server-side: the new ``GET /api/v1/auth/audit-log`` read endpoint.
* :class:`~developer_suite.services.dashboard_service.DashboardService`:
  every new card count, the recent-activity feeds, and the
  issuance/renewal split, using a lightweight fake admin client (no
  live server needed for these).
* :class:`~developer_suite.services.dashboard_refresh_service.DashboardRefreshService`:
  background-thread refresh, non-overlapping cycles, failure handling
  (``qapp`` from ``pytest-qt``, real ``QThread`` execution).
* Dashboard UI: chart widgets, quick actions, the rebuilt
  :class:`~developer_suite.ui.dashboard_page.DashboardPage`,
  navigation grouping/``SettingsModule``, and
  :class:`~developer_suite.ui.main_window.MainWindow`'s status bar.
* Isolation: Attendance Client and licensing enforcement untouched.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import uvicorn

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QDialog, QInputDialog, QLineEdit, QPushButton

from config import DatabaseConfig
from database.database import Database

import server.config as server_config_module
from server.api.app import create_app
from server.config import ServerConfig, get_server_config
from server.database.bootstrap import build_database as build_server_database
from server.models.admin_account import AdminRole
from server.services.admin_auth_service import AdminAuthService

import developer_suite.config as developer_suite_config_module
from developer_suite.admin.client import (
    AdminApiClient,
    AdminApiNotConfiguredError,
    AuditLogEntry,
    DeviceInfo,
    ServerStatus,
    SyncActivityEntry,
)
from developer_suite.config import DeveloperSuiteConfig, get_developer_suite_config
from developer_suite.database.bootstrap import build_database as build_dev_suite_database
from developer_suite.models.customer import Customer
from developer_suite.models.license import IssuedLicense
from developer_suite.services.customer_service import CustomerService
from developer_suite.services.dashboard_refresh_service import DashboardRefreshService
from developer_suite.services.dashboard_service import DashboardService
from developer_suite.services.license_service import LicenseService
from developer_suite.sync.coordinator import SyncCoordinator
from developer_suite.sync.customer_sync import register_customer_sync
from developer_suite.sync.scheduler import SyncSchedulerService
from licensing.enums import LicenseType

_STRONG_PASSWORD = "CorrectHorseBattery9!"


@pytest.fixture(autouse=True)
def _reset_developer_suite_config_singleton():
    developer_suite_config_module._config_instance = None
    yield
    developer_suite_config_module._config_instance = None


# ---------------------------------------------------------------------------
# Attendance Server fixtures (same shape as tests/test_phase10_dashboard.py
# and tests/test_phase11_server_auth.py).
# ---------------------------------------------------------------------------


@pytest.fixture
def server_config(tmp_path, monkeypatch) -> ServerConfig:
    monkeypatch.setenv("ATTENDANCE_SERVER_DB_SQLITE_PATH", str(tmp_path / "attendance_server_test.db"))
    monkeypatch.setenv("ATTENDANCE_SERVER_SECRET_KEY", "test-secret-key")
    monkeypatch.delenv("ATTENDANCE_SERVER_BOOTSTRAP_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ATTENDANCE_SERVER_BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    server_config_module._config_instance = None
    yield get_server_config()
    server_config_module._config_instance = None


@pytest.fixture
def server_database(server_config: ServerConfig) -> Database:
    database = build_server_database(server_config)
    yield database
    database.dispose()


@pytest.fixture
def auth_service(server_database: Database, server_config: ServerConfig) -> AdminAuthService:
    return AdminAuthService(server_database, config=server_config)


@pytest.fixture
def server_app(server_config: ServerConfig, server_database: Database):
    return create_app(server_config, server_database)


@pytest.fixture
def running_server_url(server_app) -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

    config = uvicorn.Config(server_app, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()

    deadline = time.monotonic() + 5.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "Attendance Server did not start within 5 seconds."

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5.0)


# ---------------------------------------------------------------------------
# Developer Suite fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture
def dev_suite_config(tmp_path, monkeypatch) -> DeveloperSuiteConfig:
    monkeypatch.setenv("DEV_SUITE_DB_SQLITE_PATH", str(tmp_path / "developer_suite_test.db"))
    developer_suite_config_module._config_instance = None
    yield get_developer_suite_config()
    developer_suite_config_module._config_instance = None


@pytest.fixture
def dev_suite_database(dev_suite_config: DeveloperSuiteConfig) -> Database:
    database = build_dev_suite_database(dev_suite_config)
    yield database
    database.dispose()


@pytest.fixture
def customer_service(dev_suite_database: Database) -> CustomerService:
    return CustomerService(dev_suite_database)


@pytest.fixture
def private_key_path(tmp_path):
    from licensing.crypto.signing import generate_keypair, save_private_key

    key_path = tmp_path / "keys" / "license_private_key.pem"
    private_key, _public_key = generate_keypair()
    save_private_key(private_key, key_path)
    return key_path


@pytest.fixture
def license_service(dev_suite_database: Database, private_key_path) -> LicenseService:
    return LicenseService(dev_suite_database, private_key_path=private_key_path)


@pytest.fixture
def sync_scheduler(dev_suite_database: Database, dev_suite_config: DeveloperSuiteConfig) -> SyncSchedulerService:
    coordinator = SyncCoordinator(dev_suite_database, dev_suite_config)
    register_customer_sync(coordinator)
    return SyncSchedulerService(coordinator, dev_suite_config)


@dataclass
class _FakeAdminClient:
    """A lightweight :class:`~developer_suite.admin.client.AdminApiClient` stand-in.

    Mirrors ``tests.test_phase10_dashboard._FakeAdminClient`` (not
    imported, to keep this file self-contained the same way every
    other phase's test file is), extended with the three Phase 12
    methods (:meth:`get_server_status`, :meth:`list_recent_activity`,
    :meth:`list_audit_log`) the expanded
    :class:`~developer_suite.services.dashboard_service.DashboardService`
    now calls.
    """

    devices: list[DeviceInfo] = field(default_factory=list)
    healthy: bool = True
    version: dict | None = field(default_factory=lambda: {"app_name": "Attendance Server", "app_version": "9.9.9"})
    raise_on_devices: bool = False
    raise_on_status: bool = False
    server_status: ServerStatus | None = None
    recent_activity: list[SyncActivityEntry] = field(default_factory=list)
    audit_log: list[AuditLogEntry] = field(default_factory=list)

    def check_health(self) -> bool:
        return self.healthy

    def get_version(self) -> dict | None:
        return self.version

    def list_devices(self) -> list[DeviceInfo]:
        if self.raise_on_devices:
            raise AdminApiNotConfiguredError("no token")
        return self.devices

    def get_server_status(self) -> ServerStatus:
        if self.raise_on_status:
            raise AdminApiNotConfiguredError("no token")
        if self.server_status is not None:
            return self.server_status
        return ServerStatus(
            app_name="Attendance Server", app_version="9.9.9", database_connected=True, uptime_seconds=1.0
        )

    def list_recent_activity(self, *, limit: int = 50) -> list[SyncActivityEntry]:
        return self.recent_activity

    def list_audit_log(self, *, limit: int = 50) -> list[AuditLogEntry]:
        return self.audit_log


def _build_service(
    customer_service: CustomerService,
    license_service: LicenseService,
    sync_scheduler: SyncSchedulerService,
    admin_client: _FakeAdminClient,
    config: DeveloperSuiteConfig,
) -> DashboardService:
    return DashboardService(customer_service, license_service, sync_scheduler, admin_client, config)


# ---------------------------------------------------------------------------
# Server: GET /api/v1/auth/audit-log.
# ---------------------------------------------------------------------------


class TestAuditLogEndpoint:
    def test_requires_authentication(self, running_server_url) -> None:
        import httpx

        response = httpx.get(f"{running_server_url}/api/v1/auth/audit-log")
        assert response.status_code == 401

    def test_viewer_scope_is_sufficient(
        self, running_server_url, auth_service: AdminAuthService
    ) -> None:
        import httpx

        auth_service.create_account(username="viewer", password=_STRONG_PASSWORD, role=AdminRole.VIEWER)
        login = httpx.post(
            f"{running_server_url}/api/v1/auth/login",
            json={"username": "viewer", "password": _STRONG_PASSWORD},
        ).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}

        response = httpx.get(f"{running_server_url}/api/v1/auth/audit-log", headers=headers)
        assert response.status_code == 200

    def test_returns_recent_entries_most_recent_first(
        self, running_server_url, auth_service: AdminAuthService
    ) -> None:
        import httpx

        auth_service.create_account(username="admin", password=_STRONG_PASSWORD, role=AdminRole.SUPER_ADMIN)
        login1 = httpx.post(
            f"{running_server_url}/api/v1/auth/login", json={"username": "admin", "password": _STRONG_PASSWORD}
        ).json()
        # A second login adds a second audit row.
        login2 = httpx.post(
            f"{running_server_url}/api/v1/auth/login", json={"username": "admin", "password": _STRONG_PASSWORD}
        ).json()
        headers = {"Authorization": f"Bearer {login2['access_token']}"}

        response = httpx.get(f"{running_server_url}/api/v1/auth/audit-log", headers=headers)
        assert response.status_code == 200
        entries = response.json()["entries"]
        assert len(entries) >= 2
        assert all(entry["action"] == "login" for entry in entries[:2])
        # Most recent first.
        assert entries[0]["id"] > entries[1]["id"]

    def test_limit_is_clamped(self, running_server_url, auth_service: AdminAuthService) -> None:
        import httpx

        auth_service.create_account(username="admin", password=_STRONG_PASSWORD, role=AdminRole.SUPER_ADMIN)
        login = httpx.post(
            f"{running_server_url}/api/v1/auth/login", json={"username": "admin", "password": _STRONG_PASSWORD}
        ).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}

        response = httpx.get(
            f"{running_server_url}/api/v1/auth/audit-log", params={"limit": 99999}, headers=headers
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# DashboardService: new card counts.
# ---------------------------------------------------------------------------


class TestDashboardServiceCardCounts:
    def test_license_type_breakdown(
        self, customer_service, license_service, sync_scheduler, dev_suite_config
    ) -> None:
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        license_service.issue_license(customer_id=customer.id, license_type=LicenseType.TRIAL)
        license_service.issue_license(customer_id=customer.id, license_type=LicenseType.MONTHLY)
        license_service.issue_license(customer_id=customer.id, license_type=LicenseType.YEARLY)
        license_service.issue_license(customer_id=customer.id, license_type=LicenseType.YEARLY)
        license_service.issue_license(customer_id=customer.id, license_type=LicenseType.LIFETIME)

        service = _build_service(customer_service, license_service, sync_scheduler, _FakeAdminClient(), dev_suite_config)
        snapshot = service.get_snapshot()

        assert snapshot.trial_licenses == 1
        assert snapshot.monthly_licenses == 1
        assert snapshot.yearly_licenses == 2
        assert snapshot.lifetime_licenses == 1

    def test_connected_devices_counts_every_device_type(
        self, customer_service, license_service, sync_scheduler, dev_suite_config
    ) -> None:
        now = datetime.now(timezone.utc)
        devices = [
            DeviceInfo("p1", "Client A", "attendance_client", True, now, now),
            DeviceInfo("p2", "Client B", "attendance_client", True, now - timedelta(hours=5), now),
            DeviceInfo("p3", "Dev Suite", "developer_suite", True, now, now),
        ]
        service = _build_service(
            customer_service, license_service, sync_scheduler, _FakeAdminClient(devices=devices), dev_suite_config
        )
        snapshot = service.get_snapshot()

        assert snapshot.connected_devices == 2  # p1 and p3 are online; p2 is stale
        assert snapshot.online_companies == 1  # only p1 counts as an online "company"

    def test_connected_devices_none_when_unconfigured(
        self, customer_service, license_service, sync_scheduler, dev_suite_config
    ) -> None:
        service = _build_service(
            customer_service, license_service, sync_scheduler, _FakeAdminClient(raise_on_devices=True), dev_suite_config
        )
        snapshot = service.get_snapshot()
        assert snapshot.connected_devices is None

    def test_database_connected_from_server_status(
        self, customer_service, license_service, sync_scheduler, dev_suite_config
    ) -> None:
        status = ServerStatus(app_name="x", app_version="1", database_connected=False, uptime_seconds=1.0)
        service = _build_service(
            customer_service,
            license_service,
            sync_scheduler,
            _FakeAdminClient(server_status=status),
            dev_suite_config,
        )
        snapshot = service.get_snapshot()
        assert snapshot.database_connected is False

    def test_database_connected_none_when_status_unavailable(
        self, customer_service, license_service, sync_scheduler, dev_suite_config
    ) -> None:
        service = _build_service(
            customer_service, license_service, sync_scheduler, _FakeAdminClient(raise_on_status=True), dev_suite_config
        )
        snapshot = service.get_snapshot()
        assert snapshot.database_connected is None
        assert snapshot.server_reachable is True  # health/version still succeeded

    def test_database_connected_none_when_server_unreachable(
        self, customer_service, license_service, sync_scheduler, dev_suite_config
    ) -> None:
        service = _build_service(
            customer_service, license_service, sync_scheduler, _FakeAdminClient(healthy=False), dev_suite_config
        )
        snapshot = service.get_snapshot()
        assert snapshot.database_connected is None
        assert snapshot.server_reachable is False


# ---------------------------------------------------------------------------
# DashboardService: recent-activity feeds.
# ---------------------------------------------------------------------------


class TestDashboardServiceRecentActivity:
    def test_recent_customer_registrations_most_recent_first(
        self, customer_service, license_service, sync_scheduler, dev_suite_config
    ) -> None:
        customer_service.create_customer(company_name="First Co", contact_name="Aaron")
        customer_service.create_customer(company_name="Second Co", contact_name="Bella")

        service = _build_service(customer_service, license_service, sync_scheduler, _FakeAdminClient(), dev_suite_config)
        snapshot = service.get_snapshot()

        names = [entry.company_name for entry in snapshot.recent_customer_registrations]
        assert names == ["Second Co", "First Co"]

    def test_recent_synchronization_passed_through_verbatim(
        self, customer_service, license_service, sync_scheduler, dev_suite_config
    ) -> None:
        now = datetime.now(timezone.utc)
        activity = [
            SyncActivityEntry(1, "customer", "abc", "create", "applied", None, 1, now),
        ]
        service = _build_service(
            customer_service,
            license_service,
            sync_scheduler,
            _FakeAdminClient(recent_activity=activity),
            dev_suite_config,
        )
        snapshot = service.get_snapshot()
        assert snapshot.recent_synchronization == activity
        assert snapshot.sync_activity_by_status[0].status_label == "applied"
        assert snapshot.sync_activity_by_status[0].count == 1

    def test_recent_server_events_from_devices_sorted_by_registration(
        self, customer_service, license_service, sync_scheduler, dev_suite_config
    ) -> None:
        now = datetime.now(timezone.utc)
        devices = [
            DeviceInfo("p1", "Older", "attendance_client", True, now, now - timedelta(days=2)),
            DeviceInfo("p2", "Newer", "attendance_client", True, now, now - timedelta(minutes=5)),
        ]
        service = _build_service(
            customer_service, license_service, sync_scheduler, _FakeAdminClient(devices=devices), dev_suite_config
        )
        snapshot = service.get_snapshot()
        names = [entry.device_name for entry in snapshot.recent_server_events]
        assert names == ["Newer", "Older"]

    def test_authentication_events_filtered_from_full_audit_log(
        self, customer_service, license_service, sync_scheduler, dev_suite_config
    ) -> None:
        now = datetime.now(timezone.utc)
        audit_log = [
            AuditLogEntry("a1", 1, "login", "Login succeeded.", now),
            AuditLogEntry("a2", 1, "password_change", None, now),
        ]
        service = _build_service(
            customer_service, license_service, sync_scheduler, _FakeAdminClient(audit_log=audit_log), dev_suite_config
        )
        snapshot = service.get_snapshot()

        assert [entry.action for entry in snapshot.recent_authentication_events] == ["login"]
        assert [entry.action for entry in snapshot.recent_audit_log] == ["login", "password_change"]

    def test_recent_feeds_empty_when_admin_client_unconfigured(
        self, customer_service, license_service, sync_scheduler, dev_suite_config
    ) -> None:
        service = _build_service(
            customer_service, license_service, sync_scheduler, _FakeAdminClient(raise_on_devices=True), dev_suite_config
        )
        snapshot = service.get_snapshot()
        assert snapshot.recent_server_events == []


# ---------------------------------------------------------------------------
# DashboardService: issuance vs. renewal split.
#
# Real elapsed time between issuing and renewing in the same test would
# be well under the renewal-detection threshold, so these tests set
# ``updated_at`` directly rather than actually sleeping — see
# developer_suite.services.dashboard_service's own module docstring for
# why the threshold exists at all.
# ---------------------------------------------------------------------------


class TestIssuanceAndRenewalSplit:
    def test_freshly_issued_license_appears_only_in_issuances(
        self, customer_service, license_service, sync_scheduler, dev_suite_config
    ) -> None:
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        license_service.issue_license(customer_id=customer.id, license_type=LicenseType.MONTHLY)

        service = _build_service(customer_service, license_service, sync_scheduler, _FakeAdminClient(), dev_suite_config)
        snapshot = service.get_snapshot()

        assert len(snapshot.recent_license_issuances) == 1
        assert snapshot.recent_license_renewals == []

    def test_renewed_license_moves_to_renewals(
        self, customer_service, license_service, sync_scheduler, dev_suite_config, dev_suite_database
    ) -> None:
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        record = license_service.issue_license(customer_id=customer.id, license_type=LicenseType.MONTHLY)
        license_service.renew_license(record.id)

        # Simulate a renewal that happened well after issuance (real
        # usage never renews within the same second it was issued).
        with dev_suite_database.session_scope() as session:
            row = session.get(IssuedLicense, record.id)
            row.updated_at = row.created_at + timedelta(days=10)

        service = _build_service(customer_service, license_service, sync_scheduler, _FakeAdminClient(), dev_suite_config)
        snapshot = service.get_snapshot()

        assert snapshot.recent_license_issuances == []
        assert len(snapshot.recent_license_renewals) == 1
        assert snapshot.recent_license_renewals[0].customer_name == "Acme Co"


# ---------------------------------------------------------------------------
# DashboardService: charts.
# ---------------------------------------------------------------------------


class TestDashboardServiceCharts:
    def test_customer_growth_is_cumulative_and_covers_the_window(
        self, customer_service, license_service, sync_scheduler, dev_suite_config
    ) -> None:
        customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        customer_service.create_customer(company_name="Beta Co", contact_name="John Roe")

        service = _build_service(customer_service, license_service, sync_scheduler, _FakeAdminClient(), dev_suite_config)
        snapshot = service.get_snapshot()

        assert len(snapshot.customer_growth) == 6  # default months window
        assert snapshot.customer_growth[-1].cumulative_customers == 2
        # Monotonically non-decreasing, since it's a cumulative count.
        counts = [point.cumulative_customers for point in snapshot.customer_growth]
        assert counts == sorted(counts)

    def test_license_distribution_covers_every_plan_in_fixed_order(
        self, customer_service, license_service, sync_scheduler, dev_suite_config
    ) -> None:
        service = _build_service(customer_service, license_service, sync_scheduler, _FakeAdminClient(), dev_suite_config)
        snapshot = service.get_snapshot()
        assert [entry.count for entry in snapshot.license_distribution] == [0, 0, 0, 0]
        assert len(snapshot.license_distribution) == 4

    def test_expiration_timeline_counts_only_active_licenses_with_an_expiry(
        self, customer_service, license_service, sync_scheduler, dev_suite_config
    ) -> None:
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        license_service.issue_license(customer_id=customer.id, license_type=LicenseType.MONTHLY, days=20)
        license_service.issue_license(customer_id=customer.id, license_type=LicenseType.LIFETIME)  # never expires

        service = _build_service(customer_service, license_service, sync_scheduler, _FakeAdminClient(), dev_suite_config)
        snapshot = service.get_snapshot()

        total_expiring = sum(bucket.count for bucket in snapshot.expiration_timeline)
        assert total_expiring == 1
        assert len(snapshot.expiration_timeline) == 6


# ---------------------------------------------------------------------------
# DashboardRefreshService.
# ---------------------------------------------------------------------------


class TestDashboardRefreshService:
    def test_start_triggers_an_immediate_refresh(self, qapp) -> None:
        from developer_suite.services.dashboard_service import DashboardSnapshot

        class _CountingService:
            def __init__(self) -> None:
                self.calls = 0

            def get_snapshot(self) -> DashboardSnapshot:
                self.calls += 1
                return DashboardSnapshot(total_customers=self.calls)

        fake = _CountingService()
        refresh_service = DashboardRefreshService(fake, interval_ms=60_000)
        received = []
        refresh_service.snapshot_ready.connect(received.append)

        refresh_service.start()
        loop = QEventLoop()
        QTimer.singleShot(500, loop.quit)
        loop.exec()
        refresh_service.stop()

        assert fake.calls == 1
        assert received[0].total_customers == 1

    def test_overlapping_refreshes_collapse_into_one(self, qapp) -> None:
        from developer_suite.services.dashboard_service import DashboardSnapshot

        class _SlowService:
            def __init__(self) -> None:
                self.calls = 0

            def get_snapshot(self) -> DashboardSnapshot:
                self.calls += 1
                time.sleep(0.3)
                return DashboardSnapshot()

        fake = _SlowService()
        refresh_service = DashboardRefreshService(fake, interval_ms=50_000)
        refresh_service.refresh_now()
        refresh_service.refresh_now()  # should be a no-op: one is already in flight
        refresh_service.refresh_now()

        loop = QEventLoop()
        QTimer.singleShot(600, loop.quit)
        loop.exec()

        assert fake.calls == 1

    def test_failure_is_swallowed_and_does_not_crash(self, qapp) -> None:
        class _FailingService:
            def get_snapshot(self):
                raise RuntimeError("boom")

        refresh_service = DashboardRefreshService(_FailingService(), interval_ms=60_000)
        received = []
        refresh_service.snapshot_ready.connect(received.append)
        refresh_service.refresh_now()

        loop = QEventLoop()
        QTimer.singleShot(400, loop.quit)
        loop.exec()
        refresh_service.stop()

        assert received == []  # never crashed the test process either


# ---------------------------------------------------------------------------
# Chart widgets.
# ---------------------------------------------------------------------------


class TestChartWidgets:
    def test_customer_growth_chart_accepts_empty_and_populated_data(self, qapp) -> None:
        from developer_suite.services.dashboard_service import CustomerGrowthPoint
        from developer_suite.ui.dashboard_charts import CustomerGrowthChart

        chart = CustomerGrowthChart()
        chart.set_data([])
        chart.set_data([CustomerGrowthPoint("2026-01", 1), CustomerGrowthPoint("2026-02", 3)])

    def test_license_distribution_chart_skips_zero_entries(self, qapp) -> None:
        from developer_suite.services.dashboard_service import LicenseDistributionEntry
        from developer_suite.ui.dashboard_charts import LicenseDistributionChart

        chart = LicenseDistributionChart()
        chart.set_data([LicenseDistributionEntry("Trial", 0), LicenseDistributionEntry("Monthly", 2)])
        assert chart._series.count() == 1

    def test_online_companies_chart_handles_none_values(self, qapp) -> None:
        from developer_suite.ui.dashboard_charts import OnlineCompaniesChart

        chart = OnlineCompaniesChart()
        chart.set_data(None, None)
        chart.set_data(3, 1)

    def test_sync_activity_chart_and_expiration_timeline_chart_populate(self, qapp) -> None:
        from developer_suite.services.dashboard_service import ExpirationTimelineBucket, SyncActivityBucket
        from developer_suite.ui.dashboard_charts import ExpirationTimelineChart, SyncActivityChart

        sync_chart = SyncActivityChart()
        sync_chart.set_data([SyncActivityBucket("applied", 3), SyncActivityBucket("conflict", 1)])

        timeline_chart = ExpirationTimelineChart()
        timeline_chart.set_data([ExpirationTimelineBucket("2026-08", 2)])


# ---------------------------------------------------------------------------
# Quick actions.
# ---------------------------------------------------------------------------


class TestQuickActionsPanel:
    def test_new_customer_creates_via_service_and_emits_completed(
        self, qapp, monkeypatch, customer_service, license_service
    ) -> None:
        import developer_suite.ui.dashboard_quick_actions as quick_actions_module

        monkeypatch.setattr(
            quick_actions_module.CustomerFormDialog,
            "exec",
            lambda self: quick_actions_module.CustomerFormDialog.DialogCode.Accepted,
        )
        monkeypatch.setattr(
            quick_actions_module.CustomerFormDialog,
            "field_values",
            lambda self: {
                "company_name": "New Co",
                "contact_name": "New Contact",
                "phone": None,
                "email": None,
                "address": None,
                "notes": None,
            },
        )

        panel = quick_actions_module.QuickActionsPanel(customer_service, license_service)
        completed = []
        panel.action_completed.connect(lambda: completed.append(True))

        panel._on_new_customer()

        assert completed == [True]
        assert len(customer_service.search_customers("New Co")) == 1

    def test_issue_license_with_no_customers_shows_message_and_does_not_crash(
        self, qapp, monkeypatch, customer_service, license_service
    ) -> None:
        import developer_suite.ui.dashboard_quick_actions as quick_actions_module

        shown = []
        monkeypatch.setattr(
            quick_actions_module.QMessageBox,
            "information",
            staticmethod(lambda *args, **kwargs: shown.append(True)),
        )

        panel = quick_actions_module.QuickActionsPanel(customer_service, license_service)
        completed = []
        panel.action_completed.connect(lambda: completed.append(True))

        panel._on_issue_license()  # no customers registered yet

        assert completed == []
        assert shown == [True]

    def test_renew_license_via_picker(
        self, qapp, monkeypatch, customer_service, license_service
    ) -> None:
        import developer_suite.ui.dashboard_quick_actions as quick_actions_module

        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        record = license_service.issue_license(customer_id=customer.id, license_type=LicenseType.MONTHLY)
        original_key = record.license_key

        monkeypatch.setattr(
            quick_actions_module.QInputDialog,
            "getItem",
            staticmethod(lambda *args, **kwargs: (kwargs.get("items", args[3] if len(args) > 3 else [])[0], True)),
        )

        panel = quick_actions_module.QuickActionsPanel(customer_service, license_service)
        completed = []
        panel.action_completed.connect(lambda: completed.append(True))

        panel._on_renew_license()

        assert completed == [True]
        renewed = license_service.get_license(record.id)
        assert renewed.license_key != original_key

    def test_suspend_customer_via_picker(
        self, qapp, monkeypatch, customer_service, license_service
    ) -> None:
        import developer_suite.ui.dashboard_quick_actions as quick_actions_module
        from developer_suite.models.customer import CustomerStatus

        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")

        monkeypatch.setattr(
            quick_actions_module.QInputDialog,
            "getItem",
            staticmethod(lambda *args, **kwargs: (kwargs.get("items", args[3] if len(args) > 3 else [])[0], True)),
        )

        panel = quick_actions_module.QuickActionsPanel(customer_service, license_service)
        completed = []
        panel.action_completed.connect(lambda: completed.append(True))

        panel._on_suspend_customer()

        assert completed == [True]
        assert customer_service.get_customer(customer.id).status is CustomerStatus.SUSPENDED

    def test_suspend_customer_with_no_active_customers_does_not_crash(
        self, qapp, monkeypatch, customer_service, license_service
    ) -> None:
        import developer_suite.ui.dashboard_quick_actions as quick_actions_module

        shown = []
        monkeypatch.setattr(
            quick_actions_module.QMessageBox,
            "information",
            staticmethod(lambda *args, **kwargs: shown.append(True)),
        )

        panel = quick_actions_module.QuickActionsPanel(customer_service, license_service)
        completed = []
        panel.action_completed.connect(lambda: completed.append(True))

        panel._on_suspend_customer()  # no customers at all

        assert completed == []
        assert shown == [True]

    def test_open_buttons_emit_navigate_requested(self, qapp, customer_service, license_service) -> None:
        from developer_suite.ui.dashboard_quick_actions import QuickActionsPanel

        panel = QuickActionsPanel(customer_service, license_service)
        navigations = []
        panel.navigate_requested.connect(navigations.append)

        buttons = {button.text(): button for button in panel.findChildren(QPushButton)}
        buttons["فتح المراقبة"].click()
        buttons["فتح الإعداد عن بُعد"].click()
        buttons["فتح إدارة التحديثات"].click()

        assert navigations == ["monitoring", "remote_configuration", "update_manager"]


# ---------------------------------------------------------------------------
# DashboardPage.
# ---------------------------------------------------------------------------


class TestDashboardPage:
    def test_populates_from_refresh_signal(
        self, qapp, customer_service, license_service, sync_scheduler, dev_suite_config
    ) -> None:
        from developer_suite.ui.dashboard_page import DashboardPage

        customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        service = _build_service(customer_service, license_service, sync_scheduler, _FakeAdminClient(), dev_suite_config)
        refresh_service = DashboardRefreshService(service)

        page = DashboardPage(refresh_service, customer_service, license_service)
        page._populate(service.get_snapshot())

        assert page._grid.count() == 14
        assert page.registrations_list.count() == 1

    def test_quick_action_navigate_requested_forwards_from_the_page(
        self, qapp, customer_service, license_service, sync_scheduler, dev_suite_config
    ) -> None:
        from developer_suite.ui.dashboard_page import DashboardPage

        service = _build_service(customer_service, license_service, sync_scheduler, _FakeAdminClient(), dev_suite_config)
        refresh_service = DashboardRefreshService(service)
        page = DashboardPage(refresh_service, customer_service, license_service)

        navigations = []
        page.navigate_requested.connect(navigations.append)
        page.quick_actions.navigate_requested.emit("monitoring")

        assert navigations == ["monitoring"]

    def test_refresh_button_calls_refresh_now(
        self, qapp, monkeypatch, customer_service, license_service, sync_scheduler, dev_suite_config
    ) -> None:
        from developer_suite.ui.dashboard_page import DashboardPage

        service = _build_service(customer_service, license_service, sync_scheduler, _FakeAdminClient(), dev_suite_config)
        refresh_service = DashboardRefreshService(service)
        page = DashboardPage(refresh_service, customer_service, license_service)

        calls = []
        monkeypatch.setattr(refresh_service, "refresh_now", lambda: calls.append(True))
        page.refresh_button.click()

        assert calls == [True]


# ---------------------------------------------------------------------------
# Navigation grouping + SettingsModule.
# ---------------------------------------------------------------------------


class TestNavigationAndModules:
    def test_all_modules_order_matches_the_requested_grouping(self) -> None:
        from developer_suite.modules import ALL_MODULES

        assert [cls.__name__ for cls in ALL_MODULES] == [
            "DashboardModule",
            "CustomerManagementModule",
            "LicenseManagerModule",
            "RemoteConfigurationModule",
            "MonitoringModule",
            "UpdateManagerModule",
            "ServerStatusModule",
            "SettingsModule",
        ]

    def test_settings_module_is_a_placeholder(self, qapp) -> None:
        from developer_suite.modules.settings import SettingsModule

        module = SettingsModule()
        assert module.module_id == "settings"
        page = module.build_page()
        assert page is not None

    def test_sidebar_renders_a_divider_before_the_administration_group(self, qapp) -> None:
        from PySide6.QtWidgets import QFrame

        from developer_suite.ui.navigation import NavigationSidebar

        entries = [
            ("dashboard", "لوحة التحكم"),
            ("customer_management", "إدارة العملاء"),
            ("server_status", "حالة الخادم"),
            ("settings", "الإعدادات"),
        ]
        sidebar = NavigationSidebar(entries)
        dividers = sidebar.findChildren(QFrame, "DeveloperSuiteNavDivider")
        assert len(dividers) == 1

    def test_sidebar_selection_emits_module_selected(self, qapp) -> None:
        from developer_suite.ui.navigation import NavigationSidebar

        entries = [("dashboard", "لوحة التحكم"), ("settings", "الإعدادات")]
        sidebar = NavigationSidebar(entries)
        selected = []
        sidebar.module_selected.connect(selected.append)

        buttons = sidebar.findChildren(QPushButton)
        buttons[-1].click()
        assert selected == ["settings"]


# ---------------------------------------------------------------------------
# MainWindow status bar.
# ---------------------------------------------------------------------------


class TestMainWindowStatusBar:
    def test_status_bar_updates_from_snapshot_ready(
        self, qapp, dev_suite_database, dev_suite_config, customer_service, license_service
    ) -> None:
        from developer_suite.container import ServiceContainer
        from developer_suite.services.dashboard_service import DashboardSnapshot
        from developer_suite.ui.main_window import MainWindow

        container = ServiceContainer(config=dev_suite_config, database=dev_suite_database)
        window = MainWindow(container)

        snapshot = DashboardSnapshot(
            server_reachable=True,
            server_version="1.2.3",
            database_connected=True,
            pending_sync_count=0,
            last_sync_at=datetime.now(timezone.utc),
        )
        window._on_snapshot_ready(snapshot)

        assert "متصل" in window.server_status_label.text()
        assert "1.2.3" in window.server_status_label.text()
        assert "متصلة" in window.database_status_label.text()

    def test_administrator_label_reflects_no_session_by_default(
        self, qapp, dev_suite_database, dev_suite_config
    ) -> None:
        from developer_suite.container import ServiceContainer
        from developer_suite.ui.main_window import MainWindow

        container = ServiceContainer(config=dev_suite_config, database=dev_suite_database)
        window = MainWindow(container)
        assert "—" in window.administrator_label.text()


# ---------------------------------------------------------------------------
# Isolation.
# ---------------------------------------------------------------------------


class TestZeroImpactOnOtherApplications:
    def test_dashboard_service_never_imports_licensing_signing_directly(self) -> None:
        import ast
        import inspect

        import developer_suite.services.dashboard_service as module

        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "licensing.crypto" not in node.module
                assert "licensing.license_generator" not in node.module
                assert "licensing.license_key" not in node.module

    def test_quick_actions_never_bypass_license_service(self) -> None:
        import ast
        import inspect

        import developer_suite.ui.dashboard_quick_actions as module

        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "licensing" not in node.module

    def test_new_server_audit_log_route_imports_nothing_from_developer_suite(self) -> None:
        import ast
        import inspect

        import server.api.routers.auth as module

        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("developer_suite")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("developer_suite")

    def test_attendance_client_own_dashboard_untouched_by_developer_suite_imports(self) -> None:
        import ast
        import inspect

        import ui.dashboard_page as attendance_dashboard_module

        tree = ast.parse(inspect.getsource(attendance_dashboard_module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("developer_suite")

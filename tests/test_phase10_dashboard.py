"""Tests for Phase 10: Developer Dashboard & Administration.

Four groups:

* Server-side: the three new read-only, ``sync:admin``-scoped
  endpoints (``GET /api/v1/devices``, ``GET /api/v1/sync/activity``,
  ``GET /api/v1/status``) — auth enforcement and correct reuse of
  existing services.
* :mod:`developer_suite.admin` — the temporary bootstrap token
  provider (encrypted persistence) and the read-only API client
  (against a real running Attendance Server, mirroring
  :mod:`tests.test_phase8_customer_sync`'s own fixtures).
* :mod:`developer_suite.services.dashboard_service` — aggregation
  correctness, using lightweight fakes for the parts that would
  otherwise require a live server.
* Light-touch UI construction/population tests (``qapp`` from
  ``pytest-qt``) for the new/modified pages, plus isolation checks.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import pytest
import uvicorn

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from config import DatabaseConfig
from database.database import Database

import server.config as server_config_module
from server.api.app import create_app
from server.auth.tokens import issue_token
from server.config import ServerConfig, get_server_config
from server.database.bootstrap import build_database as build_server_database
from server.models.device import DeviceType as ServerDeviceType
from server.services.device_service import DeviceService as ServerDeviceService
from server.services.sync_service import ChangeInput, SyncService as ServerSyncService
from server.models.sync import SyncOperation as ServerSyncOperation

import developer_suite.config as developer_suite_config_module
from developer_suite.admin.client import (
    AdminApiClient,
    AdminApiNotConfiguredError,
    AuditLogEntry,
    DeviceInfo,
    ONLINE_THRESHOLD,
    ServerStatus,
    SubscriptionInfo,
    SyncActivityEntry,
)
from developer_suite.config import DeveloperSuiteConfig, DeveloperSuitePaths, get_developer_suite_config
from developer_suite.database.bootstrap import build_database as build_dev_suite_database
from developer_suite.services.customer_service import CustomerService
from developer_suite.services.dashboard_service import DashboardService
from developer_suite.sync.coordinator import SyncCoordinator
from developer_suite.sync.customer_sync import ENTITY_TYPE as CUSTOMER_ENTITY_TYPE
from developer_suite.sync.customer_sync import register_customer_sync
from developer_suite.sync.scheduler import SyncSchedulerService


@pytest.fixture(autouse=True)
def _reset_developer_suite_config_singleton():
    developer_suite_config_module._config_instance = None
    yield
    developer_suite_config_module._config_instance = None


# ---------------------------------------------------------------------------
# Attendance Server fixtures (same shape as tests/test_phase8_customer_sync.py
# and tests/test_phase9_sync_scheduler.py).
# ---------------------------------------------------------------------------


@pytest.fixture
def server_config(tmp_path, monkeypatch) -> ServerConfig:
    monkeypatch.setenv("ATTENDANCE_SERVER_DB_SQLITE_PATH", str(tmp_path / "attendance_server_test.db"))
    monkeypatch.setenv("ATTENDANCE_SERVER_SECRET_KEY", "test-secret-key")
    server_config_module._config_instance = None
    yield get_server_config()
    server_config_module._config_instance = None


@pytest.fixture
def server_database(server_config: ServerConfig) -> Database:
    database = build_server_database(server_config)
    yield database
    database.dispose()


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


@pytest.fixture
def admin_bearer_token(server_config: ServerConfig) -> str:
    return issue_token(
        {"principal_id": "admin-1", "principal_type": "developer_suite", "scopes": ["sync:admin"]},
        config=server_config,
    )


@pytest.fixture
def server_device_service(server_database: Database, server_config: ServerConfig) -> ServerDeviceService:
    return ServerDeviceService(server_database, config=server_config)


@pytest.fixture
def server_sync_service(server_database: Database) -> ServerSyncService:
    return ServerSyncService(server_database)


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


@dataclass
class _FakeSubscriptionService:
    """A minimal :class:`~developer_suite.services.subscription_service.SubscriptionService`
    stand-in — no HTTP, no admin client, matching that service's
    ``list_subscriptions`` closely enough for :class:`~developer_suite.services.dashboard_service.DashboardService`
    and :class:`~developer_suite.ui.customer_details_dialog.CustomerDetailsDialog`,
    the only two things in this file that call it.
    """

    subscriptions: list[SubscriptionInfo] = field(default_factory=list)

    def list_subscriptions(self) -> list[SubscriptionInfo]:
        return self.subscriptions


@pytest.fixture
def subscription_service() -> _FakeSubscriptionService:
    return _FakeSubscriptionService()


def _make_subscription(
    *,
    id: int,
    company_name: str = "Acme Co",
    status: str = "active",
    is_active: bool = True,
    is_expired: bool = False,
    days_remaining: int = 30,
    max_devices: int = 5,
    max_users: int | None = None,
    device_count: int | None = None,
    created_at: datetime | None = None,
) -> SubscriptionInfo:
    """Build one :class:`~developer_suite.admin.client.SubscriptionInfo` for a test, with sensible defaults."""
    today = date.today()
    return SubscriptionInfo(
        id=id,
        company_name=company_name,
        subscription_start_date=today - timedelta(days=30),
        subscription_end_date=today + timedelta(days=days_remaining),
        status=status,
        max_devices=max_devices,
        max_users=max_users,
        is_active=is_active,
        is_expired=is_expired,
        days_remaining=days_remaining,
        device_count=device_count,
        created_at=created_at or datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Server-side admin endpoints.
# ---------------------------------------------------------------------------


class TestServerAdminEndpoints:
    def test_list_devices_requires_admin_scope(self, running_server_url) -> None:
        import httpx

        response = httpx.get(f"{running_server_url}/api/v1/devices")
        assert response.status_code == 401

    def test_list_devices_excludes_api_key_hash(
        self, running_server_url, admin_bearer_token, server_device_service
    ) -> None:
        import httpx

        server_device_service.register_device(name="Acme Co Install", device_type=ServerDeviceType.ATTENDANCE_CLIENT)
        response = httpx.get(
            f"{running_server_url}/api/v1/devices", headers={"Authorization": f"Bearer {admin_bearer_token}"}
        )
        assert response.status_code == 200
        devices = response.json()["devices"]
        assert len(devices) == 1
        assert devices[0]["name"] == "Acme Co Install"
        assert "api_key_hash" not in devices[0]

    def test_sync_activity_requires_admin_scope(self, running_server_url) -> None:
        import httpx

        response = httpx.get(f"{running_server_url}/api/v1/sync/activity")
        assert response.status_code == 401

    def test_sync_activity_returns_changes_of_any_status(
        self, running_server_url, admin_bearer_token, server_device_service, server_sync_service
    ) -> None:
        import httpx

        device, _api_key = server_device_service.register_device(
            name="Acme Co Install", device_type=ServerDeviceType.ATTENDANCE_CLIENT
        )
        payload = {"company_name": "Acme Co"}
        checksum = server_sync_service.compute_checksum(payload)
        server_sync_service.push_changes(
            device.id,
            [
                ChangeInput(
                    entity_type="widget",
                    entity_id="w-1",
                    operation=ServerSyncOperation.CREATE,
                    payload=payload,
                    checksum=checksum,
                    base_version=0,
                )
            ],
        )

        response = httpx.get(
            f"{running_server_url}/api/v1/sync/activity", headers={"Authorization": f"Bearer {admin_bearer_token}"}
        )
        assert response.status_code == 200
        changes = response.json()["changes"]
        assert len(changes) == 1
        assert changes[0]["status"] == "applied"

    def test_status_requires_admin_scope(self, running_server_url) -> None:
        import httpx

        response = httpx.get(f"{running_server_url}/api/v1/status")
        assert response.status_code == 401

    def test_status_reports_version_db_and_uptime(self, running_server_url, admin_bearer_token) -> None:
        import httpx

        response = httpx.get(
            f"{running_server_url}/api/v1/status", headers={"Authorization": f"Bearer {admin_bearer_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["app_name"]
        assert data["app_version"]
        assert data["database_connected"] is True
        assert data["uptime_seconds"] >= 0

    def test_health_and_version_remain_unauthenticated(self, running_server_url) -> None:
        import httpx

        assert httpx.get(f"{running_server_url}/health").status_code == 200
        assert httpx.get(f"{running_server_url}/version").status_code == 200


# ---------------------------------------------------------------------------
# AdminApiClient, against a real running server.
#
# The Phase 10 ConfiguredAdminTokenProvider/AdminBootstrapToken bootstrap
# mechanism that used to be tested here was replaced outright in Phase
# 11 by real authentication (see tests/test_phase11_server_auth.py and
# tests/test_phase11_developer_suite_auth.py) — AdminApiClient itself
# needs no change, since it always depended only on the
# AdminTokenProvider abstraction, exercised below via a test-only
# _StaticTokenProvider.
# ---------------------------------------------------------------------------


class _StaticTokenProvider:
    def __init__(self, token: str | None) -> None:
        self._token = token

    def get_token(self) -> str | None:
        return self._token


class TestAdminApiClient:
    def test_check_health_true_when_reachable(self, running_server_url) -> None:
        client = AdminApiClient(running_server_url, _StaticTokenProvider(None))
        assert client.check_health() is True

    def test_check_health_false_when_unreachable(self) -> None:
        client = AdminApiClient("http://127.0.0.1:1", _StaticTokenProvider(None), timeout=1.0)
        assert client.check_health() is False

    def test_get_version_when_reachable(self, running_server_url) -> None:
        client = AdminApiClient(running_server_url, _StaticTokenProvider(None))
        version = client.get_version()
        assert version is not None
        assert "app_version" in version

    def test_authenticated_call_without_token_raises_not_configured(self, running_server_url) -> None:
        client = AdminApiClient(running_server_url, _StaticTokenProvider(None))
        with pytest.raises(AdminApiNotConfiguredError):
            client.list_devices()

    def test_list_devices_round_trips(
        self, running_server_url, admin_bearer_token, server_device_service
    ) -> None:
        server_device_service.register_device(name="Acme Co Install", device_type=ServerDeviceType.ATTENDANCE_CLIENT)
        client = AdminApiClient(running_server_url, _StaticTokenProvider(admin_bearer_token))
        devices = client.list_devices()
        assert len(devices) == 1
        assert isinstance(devices[0], DeviceInfo)
        assert devices[0].name == "Acme Co Install"
        assert devices[0].device_type == "attendance_client"

    def test_get_server_status_round_trips(self, running_server_url, admin_bearer_token) -> None:
        client = AdminApiClient(running_server_url, _StaticTokenProvider(admin_bearer_token))
        status = client.get_server_status()
        assert isinstance(status, ServerStatus)
        assert status.database_connected is True

    def test_list_recent_activity_round_trips(
        self, running_server_url, admin_bearer_token, server_device_service, server_sync_service
    ) -> None:
        device, _api_key = server_device_service.register_device(
            name="Acme Co Install", device_type=ServerDeviceType.ATTENDANCE_CLIENT
        )
        payload = {"a": 1}
        server_sync_service.push_changes(
            device.id,
            [
                ChangeInput(
                    entity_type="widget",
                    entity_id="w-1",
                    operation=ServerSyncOperation.CREATE,
                    payload=payload,
                    checksum=server_sync_service.compute_checksum(payload),
                    base_version=0,
                )
            ],
        )
        client = AdminApiClient(running_server_url, _StaticTokenProvider(admin_bearer_token))
        activity = client.list_recent_activity()
        assert len(activity) == 1
        assert isinstance(activity[0], SyncActivityEntry)
        assert activity[0].status == "applied"


class TestDeviceInfoOnlineClassification:
    def _device(self, *, is_active: bool = True, last_seen_at=None) -> DeviceInfo:
        return DeviceInfo(
            public_id="p1",
            name="Acme",
            device_type="attendance_client",
            is_active=is_active,
            last_seen_at=last_seen_at,
            created_at=datetime.now(timezone.utc),
        )

    def test_never_seen_is_offline(self) -> None:
        assert self._device(last_seen_at=None).is_online() is False

    def test_inactive_is_offline_even_if_recently_seen(self) -> None:
        now = datetime.now(timezone.utc)
        assert self._device(is_active=False, last_seen_at=now).is_online(now=now) is False

    def test_recently_seen_active_device_is_online(self) -> None:
        now = datetime.now(timezone.utc)
        device = self._device(last_seen_at=now - timedelta(minutes=1))
        assert device.is_online(now=now) is True

    def test_stale_last_seen_is_offline(self) -> None:
        now = datetime.now(timezone.utc)
        device = self._device(last_seen_at=now - ONLINE_THRESHOLD - timedelta(minutes=1))
        assert device.is_online(now=now) is False


# ---------------------------------------------------------------------------
# DashboardService aggregation.
# ---------------------------------------------------------------------------


@dataclass
class _FakeAdminClient:
    devices: list[DeviceInfo] = field(default_factory=list)
    healthy: bool = True
    version: dict | None = field(default_factory=lambda: {"app_name": "Attendance Server", "app_version": "9.9.9"})
    raise_on_devices: bool = False
    server_status: ServerStatus | None = None
    recent_activity: list[SyncActivityEntry] = field(default_factory=list)
    audit_log: list[AuditLogEntry] = field(default_factory=list)

    def check_health(self) -> bool:
        return self.healthy

    def get_version(self) -> dict | None:
        return self.version

    def list_devices(self) -> list[DeviceInfo]:
        if self.raise_on_devices:
            from developer_suite.admin.client import AdminApiNotConfiguredError

            raise AdminApiNotConfiguredError("no token")
        return self.devices

    def get_server_status(self) -> ServerStatus:
        if self.server_status is not None:
            return self.server_status
        return ServerStatus(
            app_name="Attendance Server", app_version="9.9.9", database_connected=True, uptime_seconds=1.0
        )

    def list_recent_activity(self, *, limit: int = 50) -> list[SyncActivityEntry]:
        return self.recent_activity

    def list_audit_log(self, *, limit: int = 50) -> list[AuditLogEntry]:
        return self.audit_log

    def get_update_stats(self):
        from developer_suite.admin.client import UpdateStatsInfo

        return UpdateStatsInfo(
            latest_deployed_version=None,
            companies_per_version={},
            pending_count=0,
            failed_count=0,
            successful_count=0,
            average_download_progress_percent=None,
        )


class TestDashboardService:
    def _build(self, customer_service, subscription_service, sync_scheduler, admin_client, config):
        return DashboardService(customer_service, subscription_service, sync_scheduler, admin_client, config)

    def test_customer_counts(self, customer_service, subscription_service, dev_suite_database, dev_suite_config) -> None:
        customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        suspended = customer_service.create_customer(company_name="Widgets Inc", contact_name="John Roe")
        customer_service.suspend(suspended.id)

        coordinator = SyncCoordinator(dev_suite_database, dev_suite_config)
        scheduler = SyncSchedulerService(coordinator, dev_suite_config)
        service = self._build(customer_service, subscription_service, scheduler, _FakeAdminClient(), dev_suite_config)

        snapshot = service.get_snapshot()
        assert snapshot.total_customers == 2
        assert snapshot.active_customers == 1
        assert snapshot.suspended_customers == 1

    def test_subscription_counts_and_upcoming_expirations(
        self, customer_service, subscription_service, dev_suite_database, dev_suite_config
    ) -> None:
        customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        subscription_service.subscriptions = [
            _make_subscription(id=1, status="active", is_active=True, is_expired=False, days_remaining=10),
            _make_subscription(id=2, status="active", is_active=True, is_expired=False, days_remaining=5),
            _make_subscription(id=3, status="active", is_active=True, is_expired=False, days_remaining=300),
            _make_subscription(id=4, status="active", is_active=False, is_expired=True, days_remaining=-5),
        ]

        coordinator = SyncCoordinator(dev_suite_database, dev_suite_config)
        scheduler = SyncSchedulerService(coordinator, dev_suite_config)
        service = self._build(customer_service, subscription_service, scheduler, _FakeAdminClient(), dev_suite_config)

        snapshot = service.get_snapshot()
        assert snapshot.expired_subscriptions == 1
        assert snapshot.active_subscriptions == 3
        assert [entry.days_remaining for entry in snapshot.upcoming_expirations] == [5, 10]

    def test_pending_sync_count_and_last_success_pass_through(
        self, customer_service, subscription_service, dev_suite_database, dev_suite_config
    ) -> None:
        customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        coordinator = SyncCoordinator(dev_suite_database, dev_suite_config)
        scheduler = SyncSchedulerService(coordinator, dev_suite_config)
        service = self._build(customer_service, subscription_service, scheduler, _FakeAdminClient(), dev_suite_config)

        snapshot = service.get_snapshot()
        assert snapshot.pending_sync_count == 1  # the create above, never pushed
        assert snapshot.last_sync_at is None

    def test_online_offline_companies_from_admin_client(
        self, customer_service, subscription_service, dev_suite_database, dev_suite_config
    ) -> None:
        now = datetime.now(timezone.utc)
        devices = [
            DeviceInfo("p1", "A", "attendance_client", True, now, now),
            DeviceInfo("p2", "B", "attendance_client", True, now - timedelta(hours=5), now),
            DeviceInfo("p3", "C", "developer_suite", True, now, now),  # not counted as a "company"
        ]
        coordinator = SyncCoordinator(dev_suite_database, dev_suite_config)
        scheduler = SyncSchedulerService(coordinator, dev_suite_config)
        service = self._build(
            customer_service, subscription_service, scheduler, _FakeAdminClient(devices=devices), dev_suite_config
        )

        snapshot = service.get_snapshot()
        assert snapshot.online_companies == 1
        assert snapshot.offline_companies == 1

    def test_online_offline_companies_none_when_admin_client_unconfigured(
        self, customer_service, subscription_service, dev_suite_database, dev_suite_config
    ) -> None:
        coordinator = SyncCoordinator(dev_suite_database, dev_suite_config)
        scheduler = SyncSchedulerService(coordinator, dev_suite_config)
        service = self._build(
            customer_service,
            subscription_service,
            scheduler,
            _FakeAdminClient(raise_on_devices=True),
            dev_suite_config,
        )

        snapshot = service.get_snapshot()
        assert snapshot.online_companies is None
        assert snapshot.offline_companies is None

    def test_server_unreachable_reports_none_version(
        self, customer_service, subscription_service, dev_suite_database, dev_suite_config
    ) -> None:
        coordinator = SyncCoordinator(dev_suite_database, dev_suite_config)
        scheduler = SyncSchedulerService(coordinator, dev_suite_config)
        service = self._build(
            customer_service, subscription_service, scheduler, _FakeAdminClient(healthy=False), dev_suite_config
        )

        snapshot = service.get_snapshot()
        assert snapshot.server_reachable is False
        assert snapshot.server_version is None

    def test_platform_version_from_config(
        self, customer_service, subscription_service, dev_suite_database, dev_suite_config
    ) -> None:
        coordinator = SyncCoordinator(dev_suite_database, dev_suite_config)
        scheduler = SyncSchedulerService(coordinator, dev_suite_config)
        service = self._build(customer_service, subscription_service, scheduler, _FakeAdminClient(), dev_suite_config)

        snapshot = service.get_snapshot()
        assert snapshot.platform_version == dev_suite_config.app_version


# ---------------------------------------------------------------------------
# SyncCoordinator.get_entity_sync_state.
# ---------------------------------------------------------------------------


class TestGetEntitySyncState:
    def test_unknown_entity_has_no_version_and_no_pending_change(
        self, dev_suite_database, dev_suite_config
    ) -> None:
        coordinator = SyncCoordinator(dev_suite_database, dev_suite_config)
        state = coordinator.get_entity_sync_state("customer", "does-not-exist")
        assert state.known_version == 0
        assert state.pending_operation is None
        assert state.pending_status is None

    def test_pending_local_create_is_reflected(self, dev_suite_database, dev_suite_config, customer_service) -> None:
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        coordinator = SyncCoordinator(dev_suite_database, dev_suite_config)
        state = coordinator.get_entity_sync_state(CUSTOMER_ENTITY_TYPE, str(customer.public_id))
        assert state.pending_operation.value == "create"
        assert state.pending_status.value == "pending"


# ---------------------------------------------------------------------------
# Light-touch UI construction/population tests.
# ---------------------------------------------------------------------------


class TestDashboardPage:
    def test_builds_and_populates_tiles(
        self, qapp, customer_service, subscription_service, dev_suite_database, dev_suite_config
    ) -> None:
        """Superseded in shape by tests.test_phase12_developer_dashboard's own
        ``TestDashboardPage`` (14 cards, charts, activity tabs, quick
        actions) — kept here only to prove the Phase 10
        DashboardService -> DashboardPage data path still works after
        Phase 12's constructor change (refresh service instead of a
        direct DashboardService reference)."""
        from developer_suite.services.dashboard_refresh_service import DashboardRefreshService
        from developer_suite.ui.dashboard_page import DashboardPage

        customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        coordinator = SyncCoordinator(dev_suite_database, dev_suite_config)
        scheduler = SyncSchedulerService(coordinator, dev_suite_config)
        service = DashboardService(customer_service, subscription_service, scheduler, _FakeAdminClient(), dev_suite_config)
        refresh_service = DashboardRefreshService(service)

        page = DashboardPage(refresh_service, customer_service)
        page._populate(service.get_snapshot())
        assert page._grid.count() == 18


class TestCustomerDetailsDialog:
    def test_shows_company_info_and_subscription_status(
        self, qapp, customer_service, subscription_service, dev_suite_database, dev_suite_config
    ) -> None:
        from PySide6.QtWidgets import QLabel

        from developer_suite.ui.customer_details_dialog import CustomerDetailsDialog

        customer = customer_service.create_customer(
            company_name="Acme Co", contact_name="Jane Doe", notes="VIP customer"
        )
        subscription_service.subscriptions = [
            _make_subscription(
                id=1, company_name="Acme Co", status="active", is_active=True, is_expired=False, days_remaining=300
            )
        ]

        coordinator = SyncCoordinator(dev_suite_database, dev_suite_config)
        register_customer_sync(coordinator)
        dialog = CustomerDetailsDialog(customer, subscription_service, coordinator)
        assert dialog.windowTitle() == "بيانات العميل — Acme Co"

        # The subscription tab renders the matched subscription's status
        # (see developer_suite.ui.customer_details_dialog._subscription_status_label) —
        # the closest surviving equivalent to the old license-count check
        # (a subscription has no "count", just this one company-matched record).
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        assert "نشط" in labels


class TestMonitoringPage:
    def test_shows_admin_not_configured_message_when_no_token(self, qapp, running_server_url) -> None:
        from developer_suite.ui.monitoring_page import MonitoringPage

        client = AdminApiClient(running_server_url, _StaticTokenProvider(None))
        page = MonitoringPage(client)
        assert "لم يتم إعداد رمز الإدارة" in page.message_label.text()

    def test_populates_devices_table_when_configured(
        self, qapp, running_server_url, admin_bearer_token, server_device_service
    ) -> None:
        from developer_suite.ui.monitoring_page import MonitoringPage

        server_device_service.register_device(name="Acme Co Install", device_type=ServerDeviceType.ATTENDANCE_CLIENT)
        client = AdminApiClient(running_server_url, _StaticTokenProvider(admin_bearer_token))
        page = MonitoringPage(client)
        assert page.devices_table.rowCount() == 1
        assert page.message_label.text() == ""


class TestServerStatusPage:
    def test_shows_degraded_message_when_no_token(self, qapp, running_server_url) -> None:
        from developer_suite.ui.server_status_page import ServerStatusPage

        client = AdminApiClient(running_server_url, _StaticTokenProvider(None))
        page = ServerStatusPage(client, DeveloperSuiteConfig())
        assert "لم يتم إعداد رمز الإدارة" in page.message_label.text()

    def test_shows_full_status_when_configured(self, qapp, running_server_url, admin_bearer_token) -> None:
        from developer_suite.ui.server_status_page import ServerStatusPage

        client = AdminApiClient(running_server_url, _StaticTokenProvider(admin_bearer_token))
        page = ServerStatusPage(client, DeveloperSuiteConfig())
        assert page.message_label.text() == ""
        assert page.form.rowCount() == 6


# ---------------------------------------------------------------------------
# Isolation.
# ---------------------------------------------------------------------------


class TestZeroImpactOnOtherApplications:
    def test_admin_session_record_table_lives_only_in_developer_suite_schema(self) -> None:
        """Phase 10's ``admin_bootstrap_token`` table was replaced outright
        by Phase 11's ``admin_session_record`` (see
        tests.test_phase11_developer_suite_auth) — this isolation check
        follows the same table under its new name."""
        from developer_suite.database.base import Base as DeveloperSuiteBase
        from models.base import Base as AttendanceBase
        from server.database.base import Base as ServerBase

        assert "admin_session_record" in DeveloperSuiteBase.metadata.tables
        assert "admin_session_record" not in AttendanceBase.metadata.tables
        assert "admin_session_record" not in ServerBase.metadata.tables

    def test_new_server_endpoints_import_nothing_from_developer_suite(self) -> None:
        import ast
        import inspect

        import server.api.routers.devices as devices_router
        import server.api.routers.status as status_router
        import server.api.routers.sync as sync_router

        for module in (devices_router, status_router, sync_router):
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("developer_suite")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("developer_suite")

    def test_admin_client_never_names_a_specific_business_entity_in_code(self) -> None:
        import ast
        import inspect

        import developer_suite.admin.client as client_module

        tree = ast.parse(inspect.getsource(client_module))
        docstring_ids = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    docstring_ids.add(id(body[0].value))

        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                assert "customer" not in node.id.lower(), node.id
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstring_ids
            ):
                assert "customer" not in node.value.lower(), node.value

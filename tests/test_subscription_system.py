"""Tests for the server-managed subscription system.

The replacement for the retired file-based license system: company
subscriptions are stored on the Attendance Server (see
:mod:`server.models.subscription`) and validated by the Attendance
Client over HTTP at startup (see
:mod:`services.subscription_check_service`), instead of a locally
signed license file. Two parts:

* ``TestSubscriptionApi``/``TestDeviceRegistrationSubscriptionEnforcement``
  exercise the server's REST surface directly via
  :class:`fastapi.testclient.TestClient` (mirrors
  ``tests/test_server_phase7.py``'s own fixture shapes).
* ``TestSubscriptionCheckServiceEndToEnd`` exercises the real,
  unmodified stack: a genuine Attendance Server served by ``uvicorn``
  over a real loopback socket, and the real
  :class:`~sync.coordinator.ClientSyncCoordinator`/:class:`~services.subscription_check_service.SubscriptionCheckService`
  the Attendance Client itself runs at startup, against a real SQLite
  database — the same "no mocks" standard
  ``tests/test_phase8_customer_sync.py`` established, extended to
  cover the grace-period behavior explicitly requested for this
  migration.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from datetime import date, timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import uvicorn
from fastapi.testclient import TestClient

import server.config as server_config_module
from server.api.app import create_app
from server.auth.tokens import issue_token
from server.config import ServerConfig, get_server_config
from server.database.bootstrap import build_database as build_server_database

from config import DatabaseConfig
from database.database import Database
from repositories.sync_repository import ClientSyncCredentialRepository
from services.subscription_check_service import (
    DEFAULT_GRACE_PERIOD,
    SubscriptionCheckOutcome,
    SubscriptionCheckService,
)
from sync.client import SyncClientError
from sync.coordinator import ClientSyncCoordinator


def _today() -> date:
    return date.today()


# ---------------------------------------------------------------------------
# Attendance Server fixtures (mirrors tests/test_server_phase7.py's own shapes).
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
def client(server_app) -> TestClient:
    return TestClient(server_app, raise_server_exceptions=False)


@pytest.fixture
def admin_headers(server_config: ServerConfig) -> dict[str, str]:
    token = issue_token(
        {"principal_id": "admin-1", "principal_type": "developer_suite", "scopes": ["sync:admin"]},
        config=server_config,
    )
    return {"Authorization": f"Bearer {token}"}


def _create_subscription(
    client: TestClient,
    admin_headers: dict[str, str],
    *,
    company_name: str = "Acme Co",
    start: date | None = None,
    end: date | None = None,
    max_devices: int = 3,
    max_users: int | None = None,
) -> dict:
    response = client.post(
        "/api/v1/subscriptions",
        json={
            "company_name": company_name,
            "subscription_start_date": (start or _today()).isoformat(),
            "subscription_end_date": (end or _today() + timedelta(days=365)).isoformat(),
            "max_devices": max_devices,
            "max_users": max_users,
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _register_device(
    client: TestClient,
    admin_headers: dict[str, str],
    *,
    name: str,
    company_name: str | None,
):
    return client.post(
        "/api/v1/devices/register",
        json={"name": name, "device_type": "attendance_client", "company_name": company_name},
        headers=admin_headers,
    )


# ---------------------------------------------------------------------------
# Server REST API: admin CRUD + device-facing status check.
# ---------------------------------------------------------------------------


class TestSubscriptionApi:
    def test_create_returns_active_subscription(self, client: TestClient, admin_headers) -> None:
        body = _create_subscription(client, admin_headers, company_name="Acme Co", max_devices=5)
        assert body["company_name"] == "Acme Co"
        assert body["status"] == "active"
        assert body["is_active"] is True
        assert body["is_expired"] is False
        assert body["max_devices"] == 5
        assert body["max_users"] is None

    def test_create_duplicate_company_name_conflicts(self, client: TestClient, admin_headers) -> None:
        _create_subscription(client, admin_headers, company_name="Acme Co")
        response = client.post(
            "/api/v1/subscriptions",
            json={
                "company_name": "Acme Co",
                "subscription_start_date": _today().isoformat(),
                "subscription_end_date": (_today() + timedelta(days=30)).isoformat(),
                "max_devices": 1,
            },
            headers=admin_headers,
        )
        assert response.status_code == 409

    def test_create_requires_admin_scope(self, client: TestClient, server_config: ServerConfig) -> None:
        read_only_token = issue_token(
            {"principal_id": "reader", "principal_type": "developer_suite", "scopes": ["sync:read"]},
            config=server_config,
        )
        response = client.post(
            "/api/v1/subscriptions",
            json={
                "company_name": "Acme Co",
                "subscription_start_date": _today().isoformat(),
                "subscription_end_date": (_today() + timedelta(days=30)).isoformat(),
                "max_devices": 1,
            },
            headers={"Authorization": f"Bearer {read_only_token}"},
        )
        assert response.status_code == 403

    def test_list_includes_device_count(self, client: TestClient, admin_headers) -> None:
        created = _create_subscription(client, admin_headers, company_name="Acme Co", max_devices=5)
        _register_device(client, admin_headers, name="Client 1", company_name="Acme Co")

        response = client.get("/api/v1/subscriptions", headers=admin_headers)
        assert response.status_code == 200
        entries = {entry["id"]: entry for entry in response.json()["subscriptions"]}
        assert entries[created["id"]]["device_count"] == 1

    def test_get_unknown_id_is_404(self, client: TestClient, admin_headers) -> None:
        response = client.get("/api/v1/subscriptions/999999", headers=admin_headers)
        assert response.status_code == 404

    def test_suspend_then_reactivate(self, client: TestClient, admin_headers) -> None:
        created = _create_subscription(client, admin_headers, company_name="Acme Co")

        suspended = client.patch(
            f"/api/v1/subscriptions/{created['id']}", json={"action": "suspend"}, headers=admin_headers
        )
        assert suspended.status_code == 200
        assert suspended.json()["status"] == "suspended"
        assert suspended.json()["is_active"] is False

        reactivated = client.patch(
            f"/api/v1/subscriptions/{created['id']}", json={"action": "reactivate"}, headers=admin_headers
        )
        assert reactivated.status_code == 200
        assert reactivated.json()["status"] == "active"
        assert reactivated.json()["is_active"] is True

    def test_renew_extends_end_date(self, client: TestClient, admin_headers) -> None:
        created = _create_subscription(client, admin_headers, company_name="Acme Co")
        new_end = (_today() + timedelta(days=999)).isoformat()

        response = client.patch(
            f"/api/v1/subscriptions/{created['id']}",
            json={"subscription_end_date": new_end},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["subscription_end_date"] == new_end

    def test_update_limits_changes_max_devices_and_max_users(self, client: TestClient, admin_headers) -> None:
        created = _create_subscription(client, admin_headers, company_name="Acme Co", max_devices=2)

        response = client.patch(
            f"/api/v1/subscriptions/{created['id']}",
            json={"max_devices": 10, "max_users": 25},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["max_devices"] == 10
        assert response.json()["max_users"] == 25

    def test_update_limits_can_clear_max_users_back_to_unlimited(
        self, client: TestClient, admin_headers
    ) -> None:
        created = _create_subscription(client, admin_headers, company_name="Acme Co", max_users=5)

        response = client.patch(
            f"/api/v1/subscriptions/{created['id']}",
            json={"max_users_unlimited": True},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["max_users"] is None

    def test_device_facing_status_active(self, client: TestClient, admin_headers) -> None:
        _create_subscription(client, admin_headers, company_name="Acme Co", max_devices=5)
        registration = _register_device(client, admin_headers, name="Client 1", company_name="Acme Co")
        assert registration.status_code == 201
        device_id = registration.json()["device"]["public_id"]
        api_key = registration.json()["api_key"]

        response = client.get(
            "/api/v1/subscription/status",
            headers={"X-Device-Id": device_id, "X-Device-Api-Key": api_key},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "active"
        assert body["company_name"] == "Acme Co"

    def test_device_facing_status_reflects_suspension_immediately(
        self, client: TestClient, admin_headers
    ) -> None:
        created = _create_subscription(client, admin_headers, company_name="Acme Co")
        registration = _register_device(client, admin_headers, name="Client 1", company_name="Acme Co")
        device_id = registration.json()["device"]["public_id"]
        api_key = registration.json()["api_key"]

        client.patch(f"/api/v1/subscriptions/{created['id']}", json={"action": "suspend"}, headers=admin_headers)

        response = client.get(
            "/api/v1/subscription/status",
            headers={"X-Device-Id": device_id, "X-Device-Api-Key": api_key},
        )
        assert response.json()["status"] == "suspended"

    def test_device_facing_status_reflects_expiry(self, client: TestClient, admin_headers) -> None:
        _create_subscription(
            client,
            admin_headers,
            company_name="Acme Co",
            start=_today() - timedelta(days=30),
            end=_today() - timedelta(days=1),
        )
        registration = _register_device(client, admin_headers, name="Client 1", company_name="Acme Co")
        device_id = registration.json()["device"]["public_id"]
        api_key = registration.json()["api_key"]

        response = client.get(
            "/api/v1/subscription/status",
            headers={"X-Device-Id": device_id, "X-Device-Api-Key": api_key},
        )
        assert response.json()["status"] == "expired"

    def test_device_facing_status_not_linked_for_developer_suite_device(
        self, client: TestClient, admin_headers
    ) -> None:
        registration = client.post(
            "/api/v1/devices/register",
            json={"name": "Vendor Workstation", "device_type": "developer_suite"},
            headers=admin_headers,
        )
        assert registration.status_code == 201
        device_id = registration.json()["device"]["public_id"]
        api_key = registration.json()["api_key"]

        response = client.get(
            "/api/v1/subscription/status",
            headers={"X-Device-Id": device_id, "X-Device-Api-Key": api_key},
        )
        assert response.json()["status"] == "not_linked"


class TestDeviceRegistrationSubscriptionEnforcement:
    def test_unknown_company_name_is_422(self, client: TestClient, admin_headers) -> None:
        response = _register_device(client, admin_headers, name="Client 1", company_name="Nonexistent Co")
        assert response.status_code == 422

    def test_registering_without_company_name_is_not_enforced(
        self, client: TestClient, admin_headers
    ) -> None:
        """Opt-in enforcement: omitting company_name entirely bypasses the subscription check."""
        response = _register_device(client, admin_headers, name="Legacy Client", company_name=None)
        assert response.status_code == 201

    def test_max_devices_reached_is_403(self, client: TestClient, admin_headers) -> None:
        _create_subscription(client, admin_headers, company_name="Acme Co", max_devices=1)

        first = _register_device(client, admin_headers, name="Client 1", company_name="Acme Co")
        assert first.status_code == 201

        second = _register_device(client, admin_headers, name="Client 2", company_name="Acme Co")
        assert second.status_code == 403

    def test_devices_for_different_companies_do_not_share_a_cap(
        self, client: TestClient, admin_headers
    ) -> None:
        _create_subscription(client, admin_headers, company_name="Acme Co", max_devices=1)
        _create_subscription(client, admin_headers, company_name="Widgets Inc", max_devices=1)

        acme_registration = _register_device(client, admin_headers, name="Acme Client", company_name="Acme Co")
        widgets_registration = _register_device(
            client, admin_headers, name="Widgets Client", company_name="Widgets Inc"
        )
        assert acme_registration.status_code == 201
        assert widgets_registration.status_code == 201


# ---------------------------------------------------------------------------
# Attendance Client end-to-end: real server, real coordinator, real cache.
# ---------------------------------------------------------------------------


@pytest.fixture
def running_server_url(server_app) -> str:
    """Serve ``server_app`` for real over a loopback socket, for the life of one test."""
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
def client_database(tmp_path) -> Database:
    database = Database(DatabaseConfig(sqlite_path=tmp_path / "attendance_client_test.db"))
    database.initialize()
    yield database
    database.dispose()


def _create_subscription_via_admin_api(
    running_server_url: str,
    admin_bearer_token: str,
    *,
    company_name: str,
    start: date | None = None,
    end: date | None = None,
    max_devices: int = 3,
) -> dict:
    import httpx

    response = httpx.post(
        f"{running_server_url}/api/v1/subscriptions",
        json={
            "company_name": company_name,
            "subscription_start_date": (start or _today()).isoformat(),
            "subscription_end_date": (end or _today() + timedelta(days=365)).isoformat(),
            "max_devices": max_devices,
        },
        headers={"Authorization": f"Bearer {admin_bearer_token}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _patch_subscription_via_admin_api(
    running_server_url: str, admin_bearer_token: str, subscription_id: int, **fields
) -> dict:
    import httpx

    response = httpx.patch(
        f"{running_server_url}/api/v1/subscriptions/{subscription_id}",
        json=fields,
        headers={"Authorization": f"Bearer {admin_bearer_token}"},
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestSubscriptionCheckServiceEndToEnd:
    def test_not_enrolled_is_blocked_without_contacting_the_server(
        self, client_database: Database, running_server_url: str
    ) -> None:
        coordinator = ClientSyncCoordinator(client_database, running_server_url)
        service = SubscriptionCheckService(client_database, coordinator)

        result = service.check()

        assert result.allowed is False
        assert result.outcome is SubscriptionCheckOutcome.NOT_REGISTERED

    def test_active_subscription_allows_access_and_caches_the_result(
        self, client_database: Database, running_server_url: str, admin_bearer_token: str
    ) -> None:
        _create_subscription_via_admin_api(running_server_url, admin_bearer_token, company_name="Acme Co")
        coordinator = ClientSyncCoordinator(client_database, running_server_url)
        coordinator.enroll(admin_bearer_token=admin_bearer_token, name="Client 1", company_name="Acme Co")
        service = SubscriptionCheckService(client_database, coordinator)

        result = service.check()

        assert result.allowed is True
        assert result.outcome is SubscriptionCheckOutcome.VALID
        assert result.company_name == "Acme Co"

        cached = service.get_cached()
        assert cached is not None
        assert cached.status == "active"
        assert cached.company_name == "Acme Co"

    def test_suspended_subscription_blocks_immediately(
        self, client_database: Database, running_server_url: str, admin_bearer_token: str
    ) -> None:
        subscription = _create_subscription_via_admin_api(
            running_server_url, admin_bearer_token, company_name="Acme Co"
        )
        coordinator = ClientSyncCoordinator(client_database, running_server_url)
        coordinator.enroll(admin_bearer_token=admin_bearer_token, name="Client 1", company_name="Acme Co")
        service = SubscriptionCheckService(client_database, coordinator)
        assert service.check().allowed is True

        _patch_subscription_via_admin_api(
            running_server_url, admin_bearer_token, subscription["id"], action="suspend"
        )

        result = service.check()
        assert result.allowed is False
        assert result.outcome is SubscriptionCheckOutcome.SUSPENDED

    def test_reactivated_subscription_unblocks(
        self, client_database: Database, running_server_url: str, admin_bearer_token: str
    ) -> None:
        subscription = _create_subscription_via_admin_api(
            running_server_url, admin_bearer_token, company_name="Acme Co"
        )
        coordinator = ClientSyncCoordinator(client_database, running_server_url)
        coordinator.enroll(admin_bearer_token=admin_bearer_token, name="Client 1", company_name="Acme Co")
        service = SubscriptionCheckService(client_database, coordinator)
        _patch_subscription_via_admin_api(
            running_server_url, admin_bearer_token, subscription["id"], action="suspend"
        )
        assert service.check().allowed is False

        _patch_subscription_via_admin_api(
            running_server_url, admin_bearer_token, subscription["id"], action="reactivate"
        )

        result = service.check()
        assert result.allowed is True
        assert result.outcome is SubscriptionCheckOutcome.VALID

    def test_expired_subscription_blocks(
        self, client_database: Database, running_server_url: str, admin_bearer_token: str
    ) -> None:
        _create_subscription_via_admin_api(
            running_server_url,
            admin_bearer_token,
            company_name="Acme Co",
            start=_today() - timedelta(days=30),
            end=_today() - timedelta(days=1),
        )
        coordinator = ClientSyncCoordinator(client_database, running_server_url)
        coordinator.enroll(admin_bearer_token=admin_bearer_token, name="Client 1", company_name="Acme Co")
        service = SubscriptionCheckService(client_database, coordinator)

        result = service.check()
        assert result.allowed is False
        assert result.outcome is SubscriptionCheckOutcome.EXPIRED

    def test_max_devices_reached_rejects_enrollment(
        self, tmp_path, running_server_url: str, admin_bearer_token: str
    ) -> None:
        _create_subscription_via_admin_api(
            running_server_url, admin_bearer_token, company_name="Acme Co", max_devices=1
        )

        first_database = Database(DatabaseConfig(sqlite_path=tmp_path / "first.db"))
        first_database.initialize()
        first_coordinator = ClientSyncCoordinator(first_database, running_server_url)
        first_coordinator.enroll(admin_bearer_token=admin_bearer_token, name="Client 1", company_name="Acme Co")

        second_database = Database(DatabaseConfig(sqlite_path=tmp_path / "second.db"))
        second_database.initialize()
        second_coordinator = ClientSyncCoordinator(second_database, running_server_url)
        with pytest.raises(SyncClientError):
            second_coordinator.enroll(
                admin_bearer_token=admin_bearer_token, name="Client 2", company_name="Acme Co"
            )

        first_database.dispose()
        second_database.dispose()

    def _corrupt_server_url(self, client_database: Database, *, bad_url: str) -> None:
        """Point the already-enrolled credential at an unreachable address.

        Simulates "server unreachable" for :meth:`SubscriptionCheckService.check`
        without tearing down the shared ``running_server_url`` fixture
        other assertions in the same test may still need.
        """
        with client_database.session_scope() as session:
            repo = ClientSyncCredentialRepository(session)
            credential = repo.get()
            repo.save(
                device_public_id=credential.device_public_id,
                api_key=credential.api_key,
                server_url=bad_url,
            )

    def test_server_unreachable_within_grace_period_still_allows_access(
        self, client_database: Database, running_server_url: str, admin_bearer_token: str
    ) -> None:
        _create_subscription_via_admin_api(running_server_url, admin_bearer_token, company_name="Acme Co")
        coordinator = ClientSyncCoordinator(client_database, running_server_url)
        coordinator.enroll(admin_bearer_token=admin_bearer_token, name="Client 1", company_name="Acme Co")
        service = SubscriptionCheckService(client_database, coordinator, grace_period=DEFAULT_GRACE_PERIOD)
        assert service.check().outcome is SubscriptionCheckOutcome.VALID

        self._corrupt_server_url(client_database, bad_url="http://127.0.0.1:1")

        result = service.check()
        assert result.allowed is True
        assert result.outcome is SubscriptionCheckOutcome.UNREACHABLE_WITHIN_GRACE

    def test_server_unreachable_with_grace_period_expired_blocks(
        self, client_database: Database, running_server_url: str, admin_bearer_token: str
    ) -> None:
        _create_subscription_via_admin_api(running_server_url, admin_bearer_token, company_name="Acme Co")
        coordinator = ClientSyncCoordinator(client_database, running_server_url)
        coordinator.enroll(admin_bearer_token=admin_bearer_token, name="Client 1", company_name="Acme Co")
        # A zero-length grace period: the very next unreachable check
        # is already past it, without needing to fake elapsed time.
        service = SubscriptionCheckService(client_database, coordinator, grace_period=timedelta(seconds=0))
        assert service.check().outcome is SubscriptionCheckOutcome.VALID

        self._corrupt_server_url(client_database, bad_url="http://127.0.0.1:1")

        result = service.check()
        assert result.allowed is False
        assert result.outcome is SubscriptionCheckOutcome.UNREACHABLE_BLOCKED

    def test_server_unreachable_with_no_prior_cache_blocks(
        self, client_database: Database, running_server_url: str, admin_bearer_token: str
    ) -> None:
        _create_subscription_via_admin_api(running_server_url, admin_bearer_token, company_name="Acme Co")
        coordinator = ClientSyncCoordinator(client_database, running_server_url)
        coordinator.enroll(admin_bearer_token=admin_bearer_token, name="Client 1", company_name="Acme Co")
        service = SubscriptionCheckService(client_database, coordinator)

        # Corrupt the server URL before any successful check ever ran,
        # so there is nothing cached to fall back on.
        self._corrupt_server_url(client_database, bad_url="http://127.0.0.1:1")

        result = service.check()
        assert result.allowed is False
        assert result.outcome is SubscriptionCheckOutcome.UNREACHABLE_BLOCKED
        assert service.get_cached() is None

    def test_a_suspended_verdict_applies_immediately_even_within_an_unexpired_grace_window(
        self, client_database: Database, running_server_url: str, admin_bearer_token: str
    ) -> None:
        """Grace only ever covers an unreachable server, never a "no" the server already gave."""
        subscription = _create_subscription_via_admin_api(
            running_server_url, admin_bearer_token, company_name="Acme Co"
        )
        coordinator = ClientSyncCoordinator(client_database, running_server_url)
        coordinator.enroll(admin_bearer_token=admin_bearer_token, name="Client 1", company_name="Acme Co")
        service = SubscriptionCheckService(client_database, coordinator, grace_period=DEFAULT_GRACE_PERIOD)
        assert service.check().outcome is SubscriptionCheckOutcome.VALID

        _patch_subscription_via_admin_api(
            running_server_url, admin_bearer_token, subscription["id"], action="suspend"
        )
        assert service.check().outcome is SubscriptionCheckOutcome.SUSPENDED

        # Now go unreachable -- the cached verdict is "suspended," not
        # "active," so grace must not paper over it.
        self._corrupt_server_url(client_database, bad_url="http://127.0.0.1:1")
        result = service.check()
        assert result.allowed is False
        assert result.outcome is SubscriptionCheckOutcome.SUSPENDED


# ---------------------------------------------------------------------------
# Server REST API: fully-automatic self-registration (no bearer token).
# ---------------------------------------------------------------------------


class TestSelfRegistrationApi:
    def _self_register(self, client: TestClient, *, name: str, company_name: str):
        return client.post(
            "/api/v1/devices/self-register", json={"name": name, "company_name": company_name}
        )

    def test_succeeds_with_no_authorization_header_at_all(
        self, client: TestClient, admin_headers
    ) -> None:
        _create_subscription(client, admin_headers, company_name="Acme Co", max_devices=5)

        response = self._self_register(client, name="Client 1", company_name="Acme Co")

        assert response.status_code == 201
        body = response.json()
        assert body["device"]["company_name"] == "Acme Co"
        assert body["device"]["device_type"] == "attendance_client"
        assert len(body["api_key"]) > 20

    def test_unknown_company_is_422(self, client: TestClient) -> None:
        response = self._self_register(client, name="Client 1", company_name="Nonexistent Co")
        assert response.status_code == 422

    def test_suspended_subscription_rejects_new_devices(self, client: TestClient, admin_headers) -> None:
        created = _create_subscription(client, admin_headers, company_name="Acme Co")
        client.patch(f"/api/v1/subscriptions/{created['id']}", json={"action": "suspend"}, headers=admin_headers)

        response = self._self_register(client, name="Client 1", company_name="Acme Co")
        assert response.status_code == 422

    def test_expired_subscription_rejects_new_devices(self, client: TestClient, admin_headers) -> None:
        _create_subscription(
            client,
            admin_headers,
            company_name="Acme Co",
            start=_today() - timedelta(days=30),
            end=_today() - timedelta(days=1),
        )

        response = self._self_register(client, name="Client 1", company_name="Acme Co")
        assert response.status_code == 422

    def test_max_devices_reached_is_403_with_the_exact_required_message(
        self, client: TestClient, admin_headers
    ) -> None:
        _create_subscription(client, admin_headers, company_name="Acme Co", max_devices=1)
        first = self._self_register(client, name="Client 1", company_name="Acme Co")
        assert first.status_code == 201

        second = self._self_register(client, name="Client 2", company_name="Acme Co")
        assert second.status_code == 403
        assert second.json()["detail"] == "Maximum allowed devices reached."

    def test_second_device_registers_when_capacity_allows(self, client: TestClient, admin_headers) -> None:
        _create_subscription(client, admin_headers, company_name="Acme Co", max_devices=2)

        first = self._self_register(client, name="Client 1", company_name="Acme Co")
        second = self._self_register(client, name="Client 2", company_name="Acme Co")

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["device"]["public_id"] != second.json()["device"]["public_id"]

    def test_device_list_shows_company_name_for_a_self_registered_device(
        self, client: TestClient, admin_headers
    ) -> None:
        _create_subscription(client, admin_headers, company_name="Acme Co", max_devices=5)
        self._self_register(client, name="Client 1", company_name="Acme Co")

        response = client.get("/api/v1/devices", headers=admin_headers)
        assert response.status_code == 200
        devices = response.json()["devices"]
        assert any(d["company_name"] == "Acme Co" for d in devices)


# ---------------------------------------------------------------------------
# Fully-automatic client enrollment: no administrator action anywhere.
# ---------------------------------------------------------------------------


class TestAutomaticEnrollmentEndToEnd:
    def test_first_startup_registers_and_grants_access_in_one_call(
        self, client_database: Database, running_server_url: str, admin_bearer_token: str
    ) -> None:
        """The full requested flow: create company -> create subscription -> install client -> login succeeds."""
        _create_subscription_via_admin_api(running_server_url, admin_bearer_token, company_name="Acme Co")
        coordinator = ClientSyncCoordinator(client_database, running_server_url)
        assert coordinator.is_enrolled() is False

        service = SubscriptionCheckService(
            client_database, coordinator, company_name="Acme Co", device_name="New PC"
        )
        result = service.check()

        assert result.allowed is True
        assert result.outcome is SubscriptionCheckOutcome.VALID
        assert result.company_name == "Acme Co"
        assert coordinator.is_enrolled() is True

    def test_no_company_name_configured_blocks_without_contacting_the_server(
        self, client_database: Database, running_server_url: str
    ) -> None:
        coordinator = ClientSyncCoordinator(client_database, running_server_url)
        service = SubscriptionCheckService(client_database, coordinator, company_name="")

        result = service.check()

        assert result.allowed is False
        assert result.outcome is SubscriptionCheckOutcome.NOT_REGISTERED
        assert coordinator.is_enrolled() is False

    def test_unknown_company_blocks_with_a_clear_reason(
        self, client_database: Database, running_server_url: str
    ) -> None:
        coordinator = ClientSyncCoordinator(client_database, running_server_url)
        service = SubscriptionCheckService(client_database, coordinator, company_name="Nonexistent Co")

        result = service.check()

        assert result.allowed is False
        assert result.outcome is SubscriptionCheckOutcome.NO_SUBSCRIPTION_FOR_COMPANY
        assert coordinator.is_enrolled() is False

    def test_second_pc_also_registers_automatically_when_capacity_allows(
        self, tmp_path, running_server_url: str, admin_bearer_token: str
    ) -> None:
        _create_subscription_via_admin_api(
            running_server_url, admin_bearer_token, company_name="Acme Co", max_devices=2
        )

        first_database = Database(DatabaseConfig(sqlite_path=tmp_path / "first.db"))
        first_database.initialize()
        first_service = SubscriptionCheckService(
            first_database,
            ClientSyncCoordinator(first_database, running_server_url),
            company_name="Acme Co",
            device_name="PC 1",
        )

        second_database = Database(DatabaseConfig(sqlite_path=tmp_path / "second.db"))
        second_database.initialize()
        second_service = SubscriptionCheckService(
            second_database,
            ClientSyncCoordinator(second_database, running_server_url),
            company_name="Acme Co",
            device_name="PC 2",
        )

        first_result = first_service.check()
        second_result = second_service.check()

        assert first_result.allowed is True
        assert second_result.allowed is True

        first_database.dispose()
        second_database.dispose()

    def test_device_limit_reached_rejects_with_the_required_message(
        self, tmp_path, running_server_url: str, admin_bearer_token: str
    ) -> None:
        _create_subscription_via_admin_api(
            running_server_url, admin_bearer_token, company_name="Acme Co", max_devices=1
        )

        first_database = Database(DatabaseConfig(sqlite_path=tmp_path / "first.db"))
        first_database.initialize()
        first_service = SubscriptionCheckService(
            first_database,
            ClientSyncCoordinator(first_database, running_server_url),
            company_name="Acme Co",
            device_name="PC 1",
        )
        assert first_service.check().allowed is True

        second_database = Database(DatabaseConfig(sqlite_path=tmp_path / "second.db"))
        second_database.initialize()
        second_service = SubscriptionCheckService(
            second_database,
            ClientSyncCoordinator(second_database, running_server_url),
            company_name="Acme Co",
            device_name="PC 2",
        )
        second_result = second_service.check()

        assert second_result.allowed is False
        assert second_result.outcome is SubscriptionCheckOutcome.MAX_DEVICES_REACHED
        assert second_result.message_en == "Maximum allowed devices reached."

        first_database.dispose()
        second_database.dispose()

    def test_suspending_the_subscription_immediately_blocks_the_auto_registered_device(
        self, client_database: Database, running_server_url: str, admin_bearer_token: str
    ) -> None:
        subscription = _create_subscription_via_admin_api(
            running_server_url, admin_bearer_token, company_name="Acme Co"
        )
        coordinator = ClientSyncCoordinator(client_database, running_server_url)
        service = SubscriptionCheckService(
            client_database, coordinator, company_name="Acme Co", device_name="New PC"
        )
        assert service.check().allowed is True

        _patch_subscription_via_admin_api(
            running_server_url, admin_bearer_token, subscription["id"], action="suspend"
        )

        result = service.check()
        assert result.allowed is False
        assert result.outcome is SubscriptionCheckOutcome.SUSPENDED

    def test_existing_admin_registered_devices_continue_to_work_unaffected(
        self, client_database: Database, running_server_url: str, admin_bearer_token: str
    ) -> None:
        """The pre-existing admin-token enrollment path is untouched by automatic enrollment."""
        _create_subscription_via_admin_api(running_server_url, admin_bearer_token, company_name="Acme Co")
        coordinator = ClientSyncCoordinator(client_database, running_server_url)
        coordinator.enroll(admin_bearer_token=admin_bearer_token, name="Legacy Client", company_name="Acme Co")

        # No company_name configured for auto-enrollment -- irrelevant,
        # since this installation is already enrolled and check() must
        # never attempt to re-register it.
        service = SubscriptionCheckService(client_database, coordinator, company_name="")
        result = service.check()

        assert result.allowed is True
        assert result.outcome is SubscriptionCheckOutcome.VALID

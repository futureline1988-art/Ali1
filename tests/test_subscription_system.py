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

    def test_create_generates_a_unique_company_code_automatically(
        self, client: TestClient, admin_headers
    ) -> None:
        first = _create_subscription(client, admin_headers, company_name="Acme Co")
        second = _create_subscription(client, admin_headers, company_name="Widgets Inc")

        assert first["company_code"]
        assert second["company_code"]
        assert first["company_code"] != second["company_code"]
        # Never manually entered -- see SubscriptionFormDialog/CreateSubscriptionRequest,
        # neither of which accepts a company_code field at all.
        assert first["company_code"].startswith("ACMECO-")
        assert second["company_code"].startswith("WIDGETSINC-")

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


class TestInitialAdminAndSupportInfoApi:
    """The initial-administrator bootstrap credential and Support Information.

    Covers the Developer-Suite-only admin endpoints
    (``PUT``/``GET .../initial-admin``, ``PATCH .../support-info``) and
    the device-facing counterparts (``GET /api/v1/subscription/initial-admin``,
    the ``support_*`` fields on ``GET /api/v1/subscription/status``) --
    see :mod:`server.services.initial_admin_service`'s own docstring
    for why the Attendance Client can only ever read these, never
    create/change them.
    """

    def test_admin_get_before_set_is_not_configured(self, client: TestClient, admin_headers) -> None:
        created = _create_subscription(client, admin_headers, company_name="Acme Co")
        response = client.get(f"/api/v1/subscriptions/{created['id']}/initial-admin", headers=admin_headers)
        assert response.status_code == 200
        assert response.json() == {"configured": False}

    def test_set_initial_admin_then_admin_get_reflects_it_without_the_hash(
        self, client: TestClient, admin_headers
    ) -> None:
        created = _create_subscription(client, admin_headers, company_name="Acme Co")
        response = client.put(
            f"/api/v1/subscriptions/{created['id']}/initial-admin",
            json={"username": "admin", "full_name": "Company Admin", "password": "Str0ng!Passw0rd"},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"username": "admin", "full_name": "Company Admin"}

        fetched = client.get(f"/api/v1/subscriptions/{created['id']}/initial-admin", headers=admin_headers)
        assert fetched.json() == {"configured": True, "username": "admin", "full_name": "Company Admin"}

    def test_set_initial_admin_unknown_subscription_is_404(self, client: TestClient, admin_headers) -> None:
        response = client.put(
            "/api/v1/subscriptions/999999/initial-admin",
            json={"username": "admin", "full_name": "Company Admin", "password": "Str0ng!Passw0rd"},
            headers=admin_headers,
        )
        assert response.status_code == 404

    def test_set_initial_admin_weak_password_is_422(self, client: TestClient, admin_headers) -> None:
        created = _create_subscription(client, admin_headers, company_name="Acme Co")
        response = client.put(
            f"/api/v1/subscriptions/{created['id']}/initial-admin",
            json={"username": "admin", "full_name": "Company Admin", "password": "weak"},
            headers=admin_headers,
        )
        assert response.status_code == 422

    def test_set_initial_admin_requires_admin_scope(
        self, client: TestClient, admin_headers, server_config: ServerConfig
    ) -> None:
        created = _create_subscription(client, admin_headers, company_name="Acme Co")
        read_only_token = issue_token(
            {"principal_id": "reader", "principal_type": "developer_suite", "scopes": ["sync:read"]},
            config=server_config,
        )
        response = client.put(
            f"/api/v1/subscriptions/{created['id']}/initial-admin",
            json={"username": "admin", "full_name": "Company Admin", "password": "Str0ng!Passw0rd"},
            headers={"Authorization": f"Bearer {read_only_token}"},
        )
        assert response.status_code == 403

    def test_setting_again_replaces_it_in_place(self, client: TestClient, admin_headers) -> None:
        created = _create_subscription(client, admin_headers, company_name="Acme Co")
        client.put(
            f"/api/v1/subscriptions/{created['id']}/initial-admin",
            json={"username": "admin", "full_name": "Company Admin", "password": "Str0ng!Passw0rd"},
            headers=admin_headers,
        )
        response = client.put(
            f"/api/v1/subscriptions/{created['id']}/initial-admin",
            json={"username": "manager", "full_name": "New Manager", "password": "AnotherStr0ng!Pw"},
            headers=admin_headers,
        )
        assert response.status_code == 200
        fetched = client.get(f"/api/v1/subscriptions/{created['id']}/initial-admin", headers=admin_headers)
        assert fetched.json() == {"configured": True, "username": "manager", "full_name": "New Manager"}

    def test_device_facing_download_not_configured(self, client: TestClient, admin_headers) -> None:
        _create_subscription(client, admin_headers, company_name="Acme Co", max_devices=5)
        registration = _register_device(client, admin_headers, name="Client 1", company_name="Acme Co")
        device_id = registration.json()["device"]["public_id"]
        api_key = registration.json()["api_key"]

        response = client.get(
            "/api/v1/subscription/initial-admin",
            headers={"X-Device-Id": device_id, "X-Device-Api-Key": api_key},
        )
        assert response.status_code == 200
        assert response.json() == {"configured": False}

    def test_device_facing_download_returns_a_verifiable_hash_never_the_plaintext(
        self, client: TestClient, admin_headers
    ) -> None:
        from utils.security import verify_password

        created = _create_subscription(client, admin_headers, company_name="Acme Co", max_devices=5)
        client.put(
            f"/api/v1/subscriptions/{created['id']}/initial-admin",
            json={"username": "admin", "full_name": "Company Admin", "password": "Str0ng!Passw0rd"},
            headers=admin_headers,
        )
        registration = _register_device(client, admin_headers, name="Client 1", company_name="Acme Co")
        device_id = registration.json()["device"]["public_id"]
        api_key = registration.json()["api_key"]

        response = client.get(
            "/api/v1/subscription/initial-admin",
            headers={"X-Device-Id": device_id, "X-Device-Api-Key": api_key},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["configured"] is True
        assert body["username"] == "admin"
        assert body["full_name"] == "Company Admin"
        assert body["password_hash"] != "Str0ng!Passw0rd"
        assert verify_password("Str0ng!Passw0rd", body["password_hash"]) is True
        assert verify_password("wrong-password", body["password_hash"]) is False

    def test_device_facing_download_available_to_any_device_on_the_same_subscription(
        self, client: TestClient, admin_headers
    ) -> None:
        """Not consumed on fetch -- a second, independent installation can also download it."""
        created = _create_subscription(client, admin_headers, company_name="Acme Co", max_devices=5)
        client.put(
            f"/api/v1/subscriptions/{created['id']}/initial-admin",
            json={"username": "admin", "full_name": "Company Admin", "password": "Str0ng!Passw0rd"},
            headers=admin_headers,
        )
        for name in ("Client 1", "Client 2"):
            registration = _register_device(client, admin_headers, name=name, company_name="Acme Co")
            device_id = registration.json()["device"]["public_id"]
            api_key = registration.json()["api_key"]
            response = client.get(
                "/api/v1/subscription/initial-admin",
                headers={"X-Device-Id": device_id, "X-Device-Api-Key": api_key},
            )
            assert response.json()["configured"] is True

    def test_support_info_defaults_to_all_null_in_status(self, client: TestClient, admin_headers) -> None:
        _create_subscription(client, admin_headers, company_name="Acme Co", max_devices=5)
        registration = _register_device(client, admin_headers, name="Client 1", company_name="Acme Co")
        device_id = registration.json()["device"]["public_id"]
        api_key = registration.json()["api_key"]

        response = client.get(
            "/api/v1/subscription/status",
            headers={"X-Device-Id": device_id, "X-Device-Api-Key": api_key},
        )
        body = response.json()
        for field in (
            "support_phone_primary",
            "support_phone_secondary",
            "support_whatsapp",
            "support_email",
            "support_hours",
            "support_message",
        ):
            assert body[field] is None

    def test_support_info_update_is_partial_and_appears_in_status(
        self, client: TestClient, admin_headers
    ) -> None:
        created = _create_subscription(client, admin_headers, company_name="Acme Co", max_devices=5)
        registration = _register_device(client, admin_headers, name="Client 1", company_name="Acme Co")
        device_id = registration.json()["device"]["public_id"]
        api_key = registration.json()["api_key"]

        first = client.patch(
            f"/api/v1/subscriptions/{created['id']}/support-info",
            json={"support_phone_primary": "+1-555-0100", "support_email": "help@acme.example"},
            headers=admin_headers,
        )
        assert first.status_code == 200
        assert first.json()["support_phone_primary"] == "+1-555-0100"
        assert first.json()["support_email"] == "help@acme.example"

        second = client.patch(
            f"/api/v1/subscriptions/{created['id']}/support-info",
            json={"support_whatsapp": "+1-555-0199"},
            headers=admin_headers,
        )
        assert second.status_code == 200
        # Previously-set fields untouched by an update that omits them.
        assert second.json()["support_phone_primary"] == "+1-555-0100"
        assert second.json()["support_whatsapp"] == "+1-555-0199"

        status_response = client.get(
            "/api/v1/subscription/status",
            headers={"X-Device-Id": device_id, "X-Device-Api-Key": api_key},
        )
        body = status_response.json()
        assert body["support_phone_primary"] == "+1-555-0100"
        assert body["support_email"] == "help@acme.example"
        assert body["support_whatsapp"] == "+1-555-0199"
        assert body["support_phone_secondary"] is None

    def test_support_info_explicit_null_clears_a_field(self, client: TestClient, admin_headers) -> None:
        created = _create_subscription(client, admin_headers, company_name="Acme Co")
        client.patch(
            f"/api/v1/subscriptions/{created['id']}/support-info",
            json={"support_email": "help@acme.example"},
            headers=admin_headers,
        )
        response = client.patch(
            f"/api/v1/subscriptions/{created['id']}/support-info",
            json={"support_email": None},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["support_email"] is None

    def test_support_info_update_requires_admin_scope(
        self, client: TestClient, admin_headers, server_config: ServerConfig
    ) -> None:
        created = _create_subscription(client, admin_headers, company_name="Acme Co")
        read_only_token = issue_token(
            {"principal_id": "reader", "principal_type": "developer_suite", "scopes": ["sync:read"]},
            config=server_config,
        )
        response = client.patch(
            f"/api/v1/subscriptions/{created['id']}/support-info",
            json={"support_email": "help@acme.example"},
            headers={"Authorization": f"Bearer {read_only_token}"},
        )
        assert response.status_code == 403

    def test_support_info_update_unknown_subscription_is_404(self, client: TestClient, admin_headers) -> None:
        response = client.patch(
            "/api/v1/subscriptions/999999/support-info",
            json={"support_email": "help@acme.example"},
            headers=admin_headers,
        )
        assert response.status_code == 404


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


def _set_initial_admin_via_admin_api(
    running_server_url: str,
    admin_bearer_token: str,
    subscription_id: int,
    *,
    username: str,
    full_name: str,
    password: str,
) -> dict:
    import httpx

    response = httpx.put(
        f"{running_server_url}/api/v1/subscriptions/{subscription_id}/initial-admin",
        json={"username": username, "full_name": full_name, "password": password},
        headers={"Authorization": f"Bearer {admin_bearer_token}"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _set_support_info_via_admin_api(
    running_server_url: str, admin_bearer_token: str, subscription_id: int, **fields
) -> dict:
    import httpx

    response = httpx.patch(
        f"{running_server_url}/api/v1/subscriptions/{subscription_id}/support-info",
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
        assert coordinator.is_enrolled() is False

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
    def _self_register(self, client: TestClient, *, name: str, company_code: str):
        return client.post(
            "/api/v1/devices/self-register", json={"name": name, "company_code": company_code}
        )

    def test_succeeds_with_no_authorization_header_at_all(
        self, client: TestClient, admin_headers
    ) -> None:
        subscription = _create_subscription(client, admin_headers, company_name="Acme Co", max_devices=5)

        response = self._self_register(client, name="Client 1", company_code=subscription["company_code"])

        assert response.status_code == 201
        body = response.json()
        assert body["device"]["company_name"] == "Acme Co"
        assert body["device"]["device_type"] == "attendance_client"
        assert len(body["api_key"]) > 20

    def test_unknown_company_code_is_422(self, client: TestClient) -> None:
        response = self._self_register(client, name="Client 1", company_code="NOPE-000000")
        assert response.status_code == 422

    def test_suspended_subscription_rejects_new_devices(self, client: TestClient, admin_headers) -> None:
        created = _create_subscription(client, admin_headers, company_name="Acme Co")
        client.patch(f"/api/v1/subscriptions/{created['id']}", json={"action": "suspend"}, headers=admin_headers)

        response = self._self_register(client, name="Client 1", company_code=created["company_code"])
        assert response.status_code == 422

    def test_expired_subscription_rejects_new_devices(self, client: TestClient, admin_headers) -> None:
        created = _create_subscription(
            client,
            admin_headers,
            company_name="Acme Co",
            start=_today() - timedelta(days=30),
            end=_today() - timedelta(days=1),
        )

        response = self._self_register(client, name="Client 1", company_code=created["company_code"])
        assert response.status_code == 422

    def test_invalid_and_inactive_company_codes_return_the_identical_response(
        self, client: TestClient, admin_headers
    ) -> None:
        """Anti-enumeration: a caller must not tell "no such code" apart from "code exists but inactive"."""
        created = _create_subscription(client, admin_headers, company_name="Acme Co")
        client.patch(f"/api/v1/subscriptions/{created['id']}", json={"action": "suspend"}, headers=admin_headers)

        unknown_response = self._self_register(client, name="Client 1", company_code="NOPE-000000")
        inactive_response = self._self_register(client, name="Client 2", company_code=created["company_code"])

        assert unknown_response.status_code == inactive_response.status_code == 422
        assert unknown_response.json()["detail"] == inactive_response.json()["detail"]
        assert unknown_response.json()["detail"] == "Invalid or inactive company code."

    def test_max_devices_reached_is_403_with_the_exact_required_message(
        self, client: TestClient, admin_headers
    ) -> None:
        created = _create_subscription(client, admin_headers, company_name="Acme Co", max_devices=1)
        first = self._self_register(client, name="Client 1", company_code=created["company_code"])
        assert first.status_code == 201

        second = self._self_register(client, name="Client 2", company_code=created["company_code"])
        assert second.status_code == 403
        assert second.json()["detail"] == "Maximum allowed devices reached."

    def test_second_device_registers_when_capacity_allows(self, client: TestClient, admin_headers) -> None:
        created = _create_subscription(client, admin_headers, company_name="Acme Co", max_devices=2)

        first = self._self_register(client, name="Client 1", company_code=created["company_code"])
        second = self._self_register(client, name="Client 2", company_code=created["company_code"])

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["device"]["public_id"] != second.json()["device"]["public_id"]

    def test_device_list_shows_company_name_for_a_self_registered_device(
        self, client: TestClient, admin_headers
    ) -> None:
        created = _create_subscription(client, admin_headers, company_name="Acme Co", max_devices=5)
        self._self_register(client, name="Client 1", company_code=created["company_code"])

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
        subscription = _create_subscription_via_admin_api(
            running_server_url, admin_bearer_token, company_name="Acme Co"
        )
        coordinator = ClientSyncCoordinator(client_database, running_server_url)
        assert coordinator.is_enrolled() is False

        service = SubscriptionCheckService(client_database, coordinator, device_name="New PC")
        resolution = service.resolve_company_code(company_code=subscription["company_code"])

        assert resolution.blocked is None
        assert resolution.company_name == "Acme Co"
        assert coordinator.is_enrolled() is True

        # The composition root would now authenticate username/password
        # locally against company_id=1 (the local Company matching
        # "Acme Co") before ever calling this -- see ui.login_window.
        service.confirm_local_binding(company_id=1, company_code=subscription["company_code"])

        result = service.check()
        assert result.allowed is True
        assert result.outcome is SubscriptionCheckOutcome.VALID
        assert result.company_name == "Acme Co"

    def test_second_login_does_not_ask_or_re_register_and_just_checks(
        self, client_database: Database, running_server_url: str, admin_bearer_token: str
    ) -> None:
        """Future logins must not ask for the company code again — see resolve_company_code's docstring."""
        subscription = _create_subscription_via_admin_api(
            running_server_url, admin_bearer_token, company_name="Acme Co"
        )
        coordinator = ClientSyncCoordinator(client_database, running_server_url)
        service = SubscriptionCheckService(client_database, coordinator, device_name="New PC")
        first_resolution = service.resolve_company_code(company_code=subscription["company_code"])
        assert first_resolution.blocked is None
        service.confirm_local_binding(company_id=1, company_code=subscription["company_code"])

        with client_database.session_scope() as session:
            device_public_id_after_first_login = ClientSyncCredentialRepository(session).get().device_public_id

        # A different (bogus) code this time -- ignored, since this
        # device is already enrolled; it must not attempt to
        # re-register or change which company it belongs to.
        second_resolution = service.resolve_company_code(company_code="Some-Bogus-Code")

        assert second_resolution.blocked is None
        assert second_resolution.company_name == "Acme Co"
        with client_database.session_scope() as session:
            credential_repo = ClientSyncCredentialRepository(session)
            assert credential_repo.get().device_public_id == device_public_id_after_first_login
            assert credential_repo.get_bound_company_id() == 1
            assert credential_repo.get_company_code() == subscription["company_code"]

    def test_unknown_company_code_blocks_with_a_generic_reason(
        self, client_database: Database, running_server_url: str
    ) -> None:
        coordinator = ClientSyncCoordinator(client_database, running_server_url)
        service = SubscriptionCheckService(client_database, coordinator)

        resolution = service.resolve_company_code(company_code="NOPE-000000")

        assert resolution.company_name is None
        assert resolution.blocked is not None
        assert resolution.blocked.allowed is False
        assert resolution.blocked.outcome is SubscriptionCheckOutcome.INVALID_COMPANY_CODE
        assert coordinator.is_enrolled() is False

    def test_second_pc_also_registers_automatically_when_capacity_allows(
        self, tmp_path, running_server_url: str, admin_bearer_token: str
    ) -> None:
        subscription = _create_subscription_via_admin_api(
            running_server_url, admin_bearer_token, company_name="Acme Co", max_devices=2
        )

        first_database = Database(DatabaseConfig(sqlite_path=tmp_path / "first.db"))
        first_database.initialize()
        first_service = SubscriptionCheckService(
            first_database, ClientSyncCoordinator(first_database, running_server_url), device_name="PC 1"
        )

        second_database = Database(DatabaseConfig(sqlite_path=tmp_path / "second.db"))
        second_database.initialize()
        second_service = SubscriptionCheckService(
            second_database, ClientSyncCoordinator(second_database, running_server_url), device_name="PC 2"
        )

        first_resolution = first_service.resolve_company_code(company_code=subscription["company_code"])
        second_resolution = second_service.resolve_company_code(company_code=subscription["company_code"])

        assert first_resolution.blocked is None
        assert second_resolution.blocked is None

        first_database.dispose()
        second_database.dispose()

    def test_device_limit_reached_rejects_with_the_required_message(
        self, tmp_path, running_server_url: str, admin_bearer_token: str
    ) -> None:
        subscription = _create_subscription_via_admin_api(
            running_server_url, admin_bearer_token, company_name="Acme Co", max_devices=1
        )

        first_database = Database(DatabaseConfig(sqlite_path=tmp_path / "first.db"))
        first_database.initialize()
        first_service = SubscriptionCheckService(
            first_database, ClientSyncCoordinator(first_database, running_server_url), device_name="PC 1"
        )
        assert first_service.resolve_company_code(company_code=subscription["company_code"]).blocked is None

        second_database = Database(DatabaseConfig(sqlite_path=tmp_path / "second.db"))
        second_database.initialize()
        second_service = SubscriptionCheckService(
            second_database, ClientSyncCoordinator(second_database, running_server_url), device_name="PC 2"
        )
        second_resolution = second_service.resolve_company_code(company_code=subscription["company_code"])

        assert second_resolution.company_name is None
        assert second_resolution.blocked is not None
        assert second_resolution.blocked.allowed is False
        assert second_resolution.blocked.outcome is SubscriptionCheckOutcome.MAX_DEVICES_REACHED
        assert second_resolution.blocked.message_en == "Maximum allowed devices reached."

        first_database.dispose()
        second_database.dispose()

    def test_suspending_the_subscription_immediately_blocks_the_auto_registered_device(
        self, client_database: Database, running_server_url: str, admin_bearer_token: str
    ) -> None:
        subscription = _create_subscription_via_admin_api(
            running_server_url, admin_bearer_token, company_name="Acme Co"
        )
        coordinator = ClientSyncCoordinator(client_database, running_server_url)
        service = SubscriptionCheckService(client_database, coordinator, device_name="New PC")
        assert service.resolve_company_code(company_code=subscription["company_code"]).blocked is None
        service.confirm_local_binding(company_id=1, company_code=subscription["company_code"])
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

        # Never bound via a login-driven resolve_company_code call --
        # check() must still work fine for this already-enrolled installation.
        service = SubscriptionCheckService(client_database, coordinator)
        result = service.check()

        assert result.allowed is True
        assert result.outcome is SubscriptionCheckOutcome.VALID


# ---------------------------------------------------------------------------
# Initial-administrator bootstrap + Support Information: the full requested
# workflow, end to end -- new company -> new subscription -> new
# administrator (Developer Suite side) -> fresh Attendance Client -> company
# code enrollment -> automatic device registration -> initial administrator
# download -> local login -> creating additional employees locally -> offline
# login/support-info continuing to work.
# ---------------------------------------------------------------------------


class TestInitialAdminBootstrapEndToEnd:
    def _bootstrap_company_and_admin(
        self, client_database: Database, service: SubscriptionCheckService, *, company_name: str
    ) -> int:
        """Exactly what ``ui.login_window.LoginWindow._bootstrap_company_and_admin`` does, without Qt."""
        from repositories.role_repository import RoleRepository
        from services.company_service import CompanyService
        from services.user_service import UserService

        admin = service.download_initial_admin()
        assert admin.configured is True

        with client_database.session_scope() as session:
            company = CompanyService(session).create_company(name=company_name)
            role = RoleRepository(session, company_id=company.id).get_by_code("system_admin")
            UserService(session, company_id=company.id).create_bootstrap_admin(
                username=admin.username,
                full_name=admin.full_name,
                password_hash=admin.password_hash,
                role_id=role.id,
            )
            return company.id

    def test_full_workflow_new_company_to_local_login_and_additional_employees(
        self, client_database: Database, running_server_url: str, admin_bearer_token: str
    ) -> None:
        from models.enums import UserRole
        from repositories.role_repository import RoleRepository
        from services.auth_service import AuthenticationError, AuthService
        from services.user_service import UserService

        # 1. Developer Suite: new company (subscription) + new administrator.
        subscription = _create_subscription_via_admin_api(
            running_server_url, admin_bearer_token, company_name="Acme Co"
        )
        _set_initial_admin_via_admin_api(
            running_server_url,
            admin_bearer_token,
            subscription["id"],
            username="admin",
            full_name="Company Admin",
            password="Str0ng!Passw0rd",
        )
        _set_support_info_via_admin_api(
            running_server_url,
            admin_bearer_token,
            subscription["id"],
            support_phone_primary="+1-555-0100",
            support_whatsapp="+1-555-0199",
            support_message="We're happy to help!",
        )

        # 2. Fresh Attendance Client: company code enrollment -> automatic
        # device registration (self.resolve_company_code drives both).
        coordinator = ClientSyncCoordinator(client_database, running_server_url)
        service = SubscriptionCheckService(client_database, coordinator, device_name="New PC")
        assert coordinator.is_enrolled() is False

        resolution = service.resolve_company_code(company_code=subscription["company_code"])
        assert resolution.blocked is None
        assert resolution.company_name == "Acme Co"
        assert coordinator.is_enrolled() is True

        # 3. Initial administrator download + local storage (no local Company
        # existed yet for this company -- this is this installation's very
        # first enrollment for it).
        company_id = self._bootstrap_company_and_admin(client_database, service, company_name="Acme Co")
        service.confirm_local_binding(company_id=company_id, company_code=subscription["company_code"])

        # 4. Local login succeeds with the Developer-Suite-set credential,
        # and only that credential -- entirely against the local database.
        with client_database.session_scope() as session:
            admin_user = AuthService(session, company_id=company_id).login("admin", "Str0ng!Passw0rd")
            assert admin_user.username == "admin"
            assert admin_user.full_name == "Company Admin"

        with client_database.session_scope() as session:
            with pytest.raises(AuthenticationError):
                AuthService(session, company_id=company_id).login("admin", "totally-wrong-password")

        # 5. The administrator creates additional employees/users locally.
        with client_database.session_scope() as session:
            user_role = RoleRepository(session, company_id=company_id).get_by_code(UserRole.USER.value)
            new_employee_account = UserService(
                session, company_id=company_id, actor_user_id=admin_user.id
            ).create_user(
                username="jsmith",
                full_name="Jane Smith",
                password="AnotherStr0ng!Pw",
                role_id=user_role.id,
            )
            assert new_employee_account.id is not None

        with client_database.session_scope() as session:
            employee_login = AuthService(session, company_id=company_id).login("jsmith", "AnotherStr0ng!Pw")
            assert employee_login.username == "jsmith"

        # 6. A regular subscription check now succeeds and support
        # information is downloaded and cached locally.
        result = service.check()
        assert result.allowed is True
        assert result.outcome is SubscriptionCheckOutcome.VALID

        cached = service.get_cached()
        assert cached.support_phone_primary == "+1-555-0100"
        assert cached.support_whatsapp == "+1-555-0199"
        assert cached.support_message == "We're happy to help!"
        assert cached.support_phone_secondary is None

        # 7. Offline: local login keeps working purely against the local
        # database (never touches the network), and the cached subscription
        # check still allows access within the grace period even though the
        # server is now unreachable. The device's own stored server_url is
        # what an actual pull/check call uses (see
        # ClientSyncCoordinator._build_client), not the base_url a
        # coordinator happens to be constructed with, so simulating "offline"
        # means pointing that *stored* credential at an address nothing is
        # listening on.
        with client_database.session_scope() as session:
            ClientSyncCredentialRepository(session).get().server_url = "http://127.0.0.1:1"

        offline_result = service.check()
        assert offline_result.allowed is True
        assert offline_result.outcome is SubscriptionCheckOutcome.UNREACHABLE_WITHIN_GRACE

        with client_database.session_scope() as session:
            offline_login = AuthService(session, company_id=company_id).login("admin", "Str0ng!Passw0rd")
            assert offline_login.username == "admin"

        # Support info also stays visible offline, from the same cache.
        offline_cached = service.get_cached()
        assert offline_cached.support_phone_primary == "+1-555-0100"

    def test_no_initial_admin_configured_blocks_bootstrap_with_a_clear_reason(
        self, client_database: Database, running_server_url: str, admin_bearer_token: str
    ) -> None:
        """The Attendance Client must never fabricate an administrator -- it can only download one."""
        subscription = _create_subscription_via_admin_api(
            running_server_url, admin_bearer_token, company_name="Acme Co"
        )
        coordinator = ClientSyncCoordinator(client_database, running_server_url)
        service = SubscriptionCheckService(client_database, coordinator, device_name="New PC")

        resolution = service.resolve_company_code(company_code=subscription["company_code"])
        assert resolution.blocked is None

        admin = service.download_initial_admin()
        assert admin.configured is False
        assert admin.username is None
        assert admin.password_hash is None

"""Tests for Phase 11: Attendance Server admin authentication.

Server-side only (the Developer Suite half of Phase 11 — session
manager, login window — has its own test file). Three groups:

* :class:`~server.services.admin_auth_service.AdminAuthService` used
  directly against a real, disposable server database: login,
  lockout-persists-across-the-transaction-boundary (a regression test
  for a rollback bug caught and fixed during this phase), refresh
  rotation, logout, password change/reset, audit logging, role scopes.
* The ``/api/v1/auth/*`` router and the widened ``sync:read`` scope on
  the three Phase 10 read-only endpoints, against a real running
  Attendance Server (mirrors :mod:`tests.test_phase10_dashboard`'s own
  ``running_server_url`` fixture).
* First-run admin setup (:meth:`~server.services.admin_auth_service.AdminAuthService.needs_initial_setup`/
  :meth:`~server.services.admin_auth_service.AdminAuthService.bootstrap_first_admin`
  and the ``/api/v1/auth/setup-status``/``/api/v1/auth/setup`` routes)
  and schema isolation (the four new tables live only in
  :class:`server.database.base.Base`, never in the Attendance Client's
  or Developer Suite's own metadata).
"""

from __future__ import annotations

import os
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from database.database import Database

import server.config as server_config_module
from server.api.app import create_app
from server.auth.tokens import issue_token, verify_token
from server.config import ServerConfig, get_server_config
from server.database.base import Base as ServerBase
from server.database.bootstrap import build_database
from server.models.admin_account import AdminAccount, AdminRole
from server.models.admin_audit_log import AdminAuditAction, AdminAuditLog
from server.models.admin_session import AdminSession
from server.services.admin_auth_service import (
    AccountLockedError,
    AccountNotFoundError,
    AdminAuthenticationError,
    AdminAuthService,
    InvalidRefreshTokenError,
    InvalidResetTokenError,
    PasswordPolicyError,
    ROLE_SCOPES,
    SetupAlreadyCompletedError,
)

_STRONG_PASSWORD = "CorrectHorseBattery9!"
_OTHER_STRONG_PASSWORD = "TrebuchetIguana4?"


# ---------------------------------------------------------------------------
# Fixtures.
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
    database = build_database(server_config)
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
# AdminAuthService: login, lockout, refresh, logout.
# ---------------------------------------------------------------------------


class TestLoginAndLockout:
    def test_login_success_returns_working_token_pair(self, auth_service: AdminAuthService) -> None:
        auth_service.create_account(username="alice", password=_STRONG_PASSWORD, role=AdminRole.SUPER_ADMIN)
        result = auth_service.login("alice", _STRONG_PASSWORD)
        assert result.account.username == "alice"
        assert "." in result.refresh_token
        assert result.expires_in_minutes > 0

    def test_login_sets_last_login_at_and_clears_failure_counter(self, auth_service: AdminAuthService) -> None:
        auth_service.create_account(username="bob", password=_STRONG_PASSWORD)
        with pytest.raises(AdminAuthenticationError):
            auth_service.login("bob", "wrong-password")
        result = auth_service.login("bob", _STRONG_PASSWORD)
        assert result.account.last_login_at is not None
        assert result.account.failed_login_attempts == 0

    def test_unknown_username_raises_generic_error(self, auth_service: AdminAuthService) -> None:
        with pytest.raises(AdminAuthenticationError):
            auth_service.login("nobody", _STRONG_PASSWORD)

    def test_wrong_password_raises_generic_error(self, auth_service: AdminAuthService) -> None:
        auth_service.create_account(username="carol", password=_STRONG_PASSWORD)
        with pytest.raises(AdminAuthenticationError):
            auth_service.login("carol", "not-the-password")

    def test_repeated_failed_logins_lock_the_account(
        self, auth_service: AdminAuthService, server_config: ServerConfig
    ) -> None:
        """Regression test: failed-login bookkeeping must survive the
        exception raised by the same call — it must not be rolled back
        by ``Database.session_scope()``'s catch-and-rollback (see this
        service's ``login()`` docstring for the fix)."""
        auth_service.create_account(username="dave", password=_STRONG_PASSWORD)
        max_attempts = server_config.security.max_login_attempts
        for _ in range(max_attempts):
            with pytest.raises(AdminAuthenticationError):
                auth_service.login("dave", "wrong-password")

        # Even the *correct* password must now be rejected as locked.
        with pytest.raises(AccountLockedError):
            auth_service.login("dave", _STRONG_PASSWORD)

    def test_lockout_audit_rows_are_recorded(
        self, auth_service: AdminAuthService, server_database: Database, server_config: ServerConfig
    ) -> None:
        auth_service.create_account(username="erin", password=_STRONG_PASSWORD)
        for _ in range(server_config.security.max_login_attempts):
            with pytest.raises(AdminAuthenticationError):
                auth_service.login("erin", "wrong-password")

        with server_database.session_scope() as session:
            actions = [row.action for row in session.query(AdminAuditLog).all()]
        assert actions.count(AdminAuditAction.LOGIN_FAILED) == server_config.security.max_login_attempts
        assert AdminAuditAction.ACCOUNT_LOCKED in actions

    def test_login_audit_row_recorded_on_success(
        self, auth_service: AdminAuthService, server_database: Database
    ) -> None:
        auth_service.create_account(username="frank", password=_STRONG_PASSWORD)
        auth_service.login("frank", _STRONG_PASSWORD)
        with server_database.session_scope() as session:
            actions = [row.action for row in session.query(AdminAuditLog).all()]
        assert AdminAuditAction.LOGIN in actions

    def test_inactive_account_cannot_login(
        self, auth_service: AdminAuthService, server_database: Database
    ) -> None:
        account = auth_service.create_account(username="gina", password=_STRONG_PASSWORD)
        with server_database.session_scope() as session:
            row = session.get(AdminAccount, account.id)
            row.is_active = False
        with pytest.raises(AdminAuthenticationError):
            auth_service.login("gina", _STRONG_PASSWORD)

    def test_weak_password_rejected_at_account_creation(self, auth_service: AdminAuthService) -> None:
        with pytest.raises(PasswordPolicyError):
            auth_service.create_account(username="weak", password="abc")


class TestRefreshAndLogout:
    def test_refresh_rotates_token_and_invalidates_the_old_one(self, auth_service: AdminAuthService) -> None:
        auth_service.create_account(username="henry", password=_STRONG_PASSWORD)
        first = auth_service.login("henry", _STRONG_PASSWORD)

        second = auth_service.refresh(first.refresh_token)
        assert second.refresh_token != first.refresh_token

        with pytest.raises(InvalidRefreshTokenError):
            auth_service.refresh(first.refresh_token)

        # The rotated token still works.
        third = auth_service.refresh(second.refresh_token)
        assert third.refresh_token != second.refresh_token

    def test_refresh_with_malformed_token_raises(self, auth_service: AdminAuthService) -> None:
        with pytest.raises(InvalidRefreshTokenError):
            auth_service.refresh("not-a-valid-token")

    def test_refresh_with_unknown_public_id_raises(self, auth_service: AdminAuthService) -> None:
        import uuid

        with pytest.raises(InvalidRefreshTokenError):
            auth_service.refresh(f"{uuid.uuid4()}.some-secret")

    def test_logout_revokes_the_session(self, auth_service: AdminAuthService) -> None:
        auth_service.create_account(username="ivy", password=_STRONG_PASSWORD)
        result = auth_service.login("ivy", _STRONG_PASSWORD)
        auth_service.logout(result.refresh_token)
        with pytest.raises(InvalidRefreshTokenError):
            auth_service.refresh(result.refresh_token)

    def test_logout_is_silently_idempotent(self, auth_service: AdminAuthService) -> None:
        auth_service.create_account(username="jack", password=_STRONG_PASSWORD)
        result = auth_service.login("jack", _STRONG_PASSWORD)
        auth_service.logout(result.refresh_token)
        auth_service.logout(result.refresh_token)  # must not raise
        auth_service.logout("garbage-token")  # must not raise

    def test_list_sessions_returns_active_sessions(self, auth_service: AdminAuthService) -> None:
        account = auth_service.create_account(username="karen", password=_STRONG_PASSWORD)
        auth_service.login("karen", _STRONG_PASSWORD)
        auth_service.login("karen", _STRONG_PASSWORD)
        sessions = auth_service.list_sessions(account.public_id)
        assert len(sessions) == 2
        assert all(isinstance(row, AdminSession) for row in sessions)

    def test_list_sessions_unknown_account_raises(self, auth_service: AdminAuthService) -> None:
        import uuid

        with pytest.raises(AccountNotFoundError):
            auth_service.list_sessions(uuid.uuid4())


class TestPasswordChangeAndReset:
    def test_change_password_revokes_existing_sessions(self, auth_service: AdminAuthService) -> None:
        account = auth_service.create_account(username="liam", password=_STRONG_PASSWORD)
        result = auth_service.login("liam", _STRONG_PASSWORD)

        auth_service.change_password(
            account.public_id, current_password=_STRONG_PASSWORD, new_password=_OTHER_STRONG_PASSWORD
        )

        with pytest.raises(InvalidRefreshTokenError):
            auth_service.refresh(result.refresh_token)
        with pytest.raises(AdminAuthenticationError):
            auth_service.login("liam", _STRONG_PASSWORD)
        auth_service.login("liam", _OTHER_STRONG_PASSWORD)  # new password works

    def test_change_password_wrong_current_password_raises(self, auth_service: AdminAuthService) -> None:
        account = auth_service.create_account(username="mia", password=_STRONG_PASSWORD)
        with pytest.raises(AdminAuthenticationError):
            auth_service.change_password(
                account.public_id, current_password="nope", new_password=_OTHER_STRONG_PASSWORD
            )

    def test_change_password_weak_new_password_raises(self, auth_service: AdminAuthService) -> None:
        account = auth_service.create_account(username="noah", password=_STRONG_PASSWORD)
        with pytest.raises(PasswordPolicyError):
            auth_service.change_password(
                account.public_id, current_password=_STRONG_PASSWORD, new_password="weak"
            )

    def test_password_reset_full_cycle(
        self, auth_service: AdminAuthService, server_config: ServerConfig
    ) -> None:
        account = auth_service.create_account(username="olivia", password=_STRONG_PASSWORD)
        # Lock the account first, to confirm reset also clears lockout state.
        for _ in range(server_config.security.max_login_attempts):
            with pytest.raises(AdminAuthenticationError):
                auth_service.login("olivia", "wrong-password")

        reset_token = auth_service.request_password_reset("olivia")
        assert reset_token is not None

        auth_service.complete_password_reset(reset_token, _OTHER_STRONG_PASSWORD)

        with pytest.raises(AdminAuthenticationError):
            auth_service.login("olivia", _STRONG_PASSWORD)
        result = auth_service.login("olivia", _OTHER_STRONG_PASSWORD)
        assert result.account.username == "olivia"
        assert not result.account.is_locked

        with pytest.raises(InvalidResetTokenError):
            auth_service.complete_password_reset(reset_token, "AnotherStrongPass1!")

    def test_password_reset_unknown_username_returns_none(self, auth_service: AdminAuthService) -> None:
        assert auth_service.request_password_reset("does-not-exist") is None

    def test_password_reset_malformed_token_raises(self, auth_service: AdminAuthService) -> None:
        with pytest.raises(InvalidResetTokenError):
            auth_service.complete_password_reset("garbage", _OTHER_STRONG_PASSWORD)


class TestRoleScopes:
    def test_super_admin_gets_admin_and_read_scopes(self, auth_service: AdminAuthService) -> None:
        auth_service.create_account(username="peter", password=_STRONG_PASSWORD, role=AdminRole.SUPER_ADMIN)
        result = auth_service.login("peter", _STRONG_PASSWORD)
        claims = verify_token(result.access_token, config=auth_service._config)
        assert set(claims["scopes"]) == {"sync:admin", "sync:read"}

    def test_viewer_gets_only_read_scope(self, auth_service: AdminAuthService) -> None:
        auth_service.create_account(username="quinn", password=_STRONG_PASSWORD, role=AdminRole.VIEWER)
        result = auth_service.login("quinn", _STRONG_PASSWORD)
        claims = verify_token(result.access_token, config=auth_service._config)
        assert set(claims["scopes"]) == {"sync:read"}

    def test_role_scopes_mapping_covers_every_role(self) -> None:
        assert set(ROLE_SCOPES) == set(AdminRole)


# ---------------------------------------------------------------------------
# HTTP router, against a real running server.
# ---------------------------------------------------------------------------


class TestAuthRouter:
    def test_login_endpoint_success(self, running_server_url, auth_service: AdminAuthService) -> None:
        auth_service.create_account(username="rachel", password=_STRONG_PASSWORD)
        response = httpx.post(
            f"{running_server_url}/api/v1/auth/login",
            json={"username": "rachel", "password": _STRONG_PASSWORD},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert "password_hash" not in body["account"]

    def test_login_endpoint_wrong_password_is_401(self, running_server_url, auth_service: AdminAuthService) -> None:
        auth_service.create_account(username="sam", password=_STRONG_PASSWORD)
        response = httpx.post(
            f"{running_server_url}/api/v1/auth/login", json={"username": "sam", "password": "nope"}
        )
        assert response.status_code == 401

    def test_login_endpoint_locked_account_is_423(
        self, running_server_url, auth_service: AdminAuthService, server_config: ServerConfig
    ) -> None:
        auth_service.create_account(username="tara", password=_STRONG_PASSWORD)
        for _ in range(server_config.security.max_login_attempts):
            httpx.post(
                f"{running_server_url}/api/v1/auth/login", json={"username": "tara", "password": "nope"}
            )
        response = httpx.post(
            f"{running_server_url}/api/v1/auth/login",
            json={"username": "tara", "password": _STRONG_PASSWORD},
        )
        assert response.status_code == 423

    def test_refresh_and_logout_endpoints(self, running_server_url, auth_service: AdminAuthService) -> None:
        auth_service.create_account(username="uma", password=_STRONG_PASSWORD)
        login = httpx.post(
            f"{running_server_url}/api/v1/auth/login", json={"username": "uma", "password": _STRONG_PASSWORD}
        ).json()

        refreshed = httpx.post(
            f"{running_server_url}/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}
        )
        assert refreshed.status_code == 200

        stale = httpx.post(
            f"{running_server_url}/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}
        )
        assert stale.status_code == 401

        logout = httpx.post(
            f"{running_server_url}/api/v1/auth/logout",
            json={"refresh_token": refreshed.json()["refresh_token"]},
        )
        assert logout.status_code == 204

    def test_change_password_requires_auth(self, running_server_url) -> None:
        response = httpx.post(
            f"{running_server_url}/api/v1/auth/change-password",
            json={"current_password": "a", "new_password": _OTHER_STRONG_PASSWORD},
        )
        assert response.status_code == 401

    def test_change_password_rejects_non_admin_principal(self, running_server_url, server_config: ServerConfig) -> None:
        token = issue_token(
            {"principal_id": "some-device", "principal_type": "developer_suite", "scopes": ["sync:admin"]},
            config=server_config,
        )
        response = httpx.post(
            f"{running_server_url}/api/v1/auth/change-password",
            json={"current_password": "a", "new_password": _OTHER_STRONG_PASSWORD},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403

    def test_change_password_and_sessions_endpoints(self, running_server_url, auth_service: AdminAuthService) -> None:
        auth_service.create_account(username="vera", password=_STRONG_PASSWORD)
        login = httpx.post(
            f"{running_server_url}/api/v1/auth/login", json={"username": "vera", "password": _STRONG_PASSWORD}
        ).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}

        sessions = httpx.get(f"{running_server_url}/api/v1/auth/sessions", headers=headers)
        assert sessions.status_code == 200
        assert len(sessions.json()["sessions"]) == 1
        assert "refresh_token_hash" not in sessions.json()["sessions"][0]

        changed = httpx.post(
            f"{running_server_url}/api/v1/auth/change-password",
            json={"current_password": _STRONG_PASSWORD, "new_password": _OTHER_STRONG_PASSWORD},
            headers=headers,
        )
        assert changed.status_code == 204

        relogin = httpx.post(
            f"{running_server_url}/api/v1/auth/login",
            json={"username": "vera", "password": _OTHER_STRONG_PASSWORD},
        )
        assert relogin.status_code == 200

    def test_password_reset_endpoints(self, running_server_url, auth_service: AdminAuthService) -> None:
        auth_service.create_account(username="walt", password=_STRONG_PASSWORD)

        unknown = httpx.post(
            f"{running_server_url}/api/v1/auth/password-reset/request", json={"username": "nobody"}
        )
        assert unknown.status_code == 200
        assert unknown.json()["reset_token"] is None

        known = httpx.post(
            f"{running_server_url}/api/v1/auth/password-reset/request", json={"username": "walt"}
        )
        assert known.status_code == 200
        reset_token = known.json()["reset_token"]
        assert reset_token is not None

        completed = httpx.post(
            f"{running_server_url}/api/v1/auth/password-reset/complete",
            json={"reset_token": reset_token, "new_password": _OTHER_STRONG_PASSWORD},
        )
        assert completed.status_code == 204

        bad = httpx.post(
            f"{running_server_url}/api/v1/auth/password-reset/complete",
            json={"reset_token": "garbage", "new_password": _OTHER_STRONG_PASSWORD},
        )
        assert bad.status_code == 400


class TestSyncReadScope:
    """The three Phase 10 read-only endpoints must accept ``sync:read`` too,
    while write/admin-only endpoints must keep rejecting it."""

    def test_viewer_token_can_read_devices_status_and_activity(
        self, running_server_url, auth_service: AdminAuthService
    ) -> None:
        auth_service.create_account(username="xena", password=_STRONG_PASSWORD, role=AdminRole.VIEWER)
        login = httpx.post(
            f"{running_server_url}/api/v1/auth/login", json={"username": "xena", "password": _STRONG_PASSWORD}
        ).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}

        assert httpx.get(f"{running_server_url}/api/v1/devices", headers=headers).status_code == 200
        assert httpx.get(f"{running_server_url}/api/v1/status", headers=headers).status_code == 200
        assert httpx.get(f"{running_server_url}/api/v1/sync/activity", headers=headers).status_code == 200

    def test_viewer_token_cannot_register_devices_or_touch_conflicts(
        self, running_server_url, auth_service: AdminAuthService
    ) -> None:
        auth_service.create_account(username="yusuf", password=_STRONG_PASSWORD, role=AdminRole.VIEWER)
        login = httpx.post(
            f"{running_server_url}/api/v1/auth/login", json={"username": "yusuf", "password": _STRONG_PASSWORD}
        ).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}

        register = httpx.post(
            f"{running_server_url}/api/v1/devices/register",
            json={"name": "some-device", "device_type": "attendance_client"},
            headers=headers,
        )
        assert register.status_code == 403

        conflicts = httpx.get(f"{running_server_url}/api/v1/sync/conflicts", headers=headers)
        assert conflicts.status_code == 403

    def test_super_admin_token_still_works_on_admin_only_routes(
        self, running_server_url, auth_service: AdminAuthService
    ) -> None:
        auth_service.create_account(username="zoe", password=_STRONG_PASSWORD, role=AdminRole.SUPER_ADMIN)
        login = httpx.post(
            f"{running_server_url}/api/v1/auth/login", json={"username": "zoe", "password": _STRONG_PASSWORD}
        ).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}

        register = httpx.post(
            f"{running_server_url}/api/v1/devices/register",
            json={"name": "some-device", "device_type": "attendance_client"},
            headers=headers,
        )
        assert register.status_code == 201


# ---------------------------------------------------------------------------
# First-run admin setup.
# ---------------------------------------------------------------------------


class TestFirstRunSetup:
    def test_build_database_never_creates_an_account(self, server_database: Database) -> None:
        """build_database() itself is not the account-provisioning path any more.

        Regression coverage for the removed environment-variable
        bootstrap seeding (``ATTENDANCE_SERVER_BOOTSTRAP_ADMIN_USERNAME``/
        ``_PASSWORD``) — see ``server/database/bootstrap.py``'s module
        docstring for why that was replaced with this interactive flow.
        """
        with server_database.session_scope() as session:
            assert session.query(AdminAccount).count() == 0

    def test_needs_initial_setup_true_on_empty_database(self, auth_service: AdminAuthService) -> None:
        assert auth_service.needs_initial_setup() is True

    def test_needs_initial_setup_false_once_an_account_exists(self, auth_service: AdminAuthService) -> None:
        auth_service.create_account(username="anyone", password=_STRONG_PASSWORD)
        assert auth_service.needs_initial_setup() is False

    def test_bootstrap_first_admin_creates_super_admin_and_working_session(
        self, auth_service: AdminAuthService, server_database: Database
    ) -> None:
        result = auth_service.bootstrap_first_admin(
            username="owner", password=_STRONG_PASSWORD, full_name="System Owner"
        )
        assert result.account.username == "owner"
        assert result.account.role == AdminRole.SUPER_ADMIN
        assert result.account.full_name == "System Owner"
        assert "." in result.refresh_token

        with server_database.session_scope() as session:
            assert session.query(AdminAccount).count() == 1

        # The returned session is immediately usable, exactly like login().
        refreshed = auth_service.refresh(result.refresh_token)
        assert refreshed.account.username == "owner"

    def test_bootstrap_first_admin_refuses_once_an_account_exists(
        self, auth_service: AdminAuthService, server_database: Database
    ) -> None:
        auth_service.create_account(username="first", password=_STRONG_PASSWORD)
        with pytest.raises(SetupAlreadyCompletedError):
            auth_service.bootstrap_first_admin(username="second", password=_OTHER_STRONG_PASSWORD)

        # The rejected attempt must not have created a second account.
        with server_database.session_scope() as session:
            assert session.query(AdminAccount).count() == 1

    def test_bootstrap_first_admin_weak_password_raises_and_creates_nothing(
        self, auth_service: AdminAuthService, server_database: Database
    ) -> None:
        with pytest.raises(PasswordPolicyError):
            auth_service.bootstrap_first_admin(username="owner", password="abc")
        with server_database.session_scope() as session:
            assert session.query(AdminAccount).count() == 0

    def test_setup_status_endpoint_reflects_account_existence(
        self, running_server_url, auth_service: AdminAuthService
    ) -> None:
        before = httpx.get(f"{running_server_url}/api/v1/auth/setup-status")
        assert before.status_code == 200
        assert before.json() == {"setup_required": True}

        auth_service.create_account(username="someone", password=_STRONG_PASSWORD)

        after = httpx.get(f"{running_server_url}/api/v1/auth/setup-status")
        assert after.json() == {"setup_required": False}

    def test_setup_endpoint_creates_account_and_returns_a_session(self, running_server_url) -> None:
        response = httpx.post(
            f"{running_server_url}/api/v1/auth/setup",
            json={"username": "owner", "password": _STRONG_PASSWORD, "full_name": "System Owner"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["account"]["username"] == "owner"
        assert body["account"]["role"] == "super_admin"
        assert "password_hash" not in body["account"]

    def test_setup_endpoint_is_409_once_an_account_exists(
        self, running_server_url, auth_service: AdminAuthService
    ) -> None:
        auth_service.create_account(username="first", password=_STRONG_PASSWORD)
        response = httpx.post(
            f"{running_server_url}/api/v1/auth/setup",
            json={"username": "second", "password": _OTHER_STRONG_PASSWORD},
        )
        assert response.status_code == 409

    def test_setup_endpoint_weak_password_is_422(self, running_server_url) -> None:
        response = httpx.post(
            f"{running_server_url}/api/v1/auth/setup", json={"username": "owner", "password": "abc"}
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Schema isolation.
# ---------------------------------------------------------------------------


class TestSchemaIsolation:
    def test_admin_tables_live_only_in_server_base(self) -> None:
        table_names = set(ServerBase.metadata.tables)
        assert {
            "admin_accounts",
            "admin_sessions",
            "admin_password_reset_tokens",
            "admin_audit_logs",
        } <= table_names

    def test_admin_tables_absent_from_attendance_client_schema(self) -> None:
        from models.base import Base as ClientBase

        assert "admin_accounts" not in ClientBase.metadata.tables
        assert "admin_sessions" not in ClientBase.metadata.tables

    def test_admin_tables_absent_from_developer_suite_schema(self) -> None:
        from developer_suite.database.base import Base as DevSuiteBase

        assert "admin_accounts" not in DevSuiteBase.metadata.tables
        assert "admin_sessions" not in DevSuiteBase.metadata.tables

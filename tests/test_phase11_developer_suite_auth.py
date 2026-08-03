"""Tests for Phase 11: Developer Suite admin authentication.

Four groups, mirroring :mod:`tests.test_phase11_server_auth`'s own
structure:

* :class:`~developer_suite.models.admin_session.AdminSessionRecord` +
  :class:`~developer_suite.repositories.admin_session_repository.AdminSessionRecordRepository`
  — encrypted persistence of the singleton "remembered session" row.
* :class:`~developer_suite.admin.auth_client.AdminAuthClient` against a
  real running Attendance Server (mirrors
  :mod:`tests.test_phase10_dashboard`'s ``running_server_url`` fixture).
* :class:`~developer_suite.admin.session_manager.AdminSessionManager`
  — login/remember-me, automatic refresh, auto-login, logout, session
  expiration handling — the real
  :class:`~developer_suite.admin.token_provider.AdminTokenProvider`
  implementation that replaces Phase 10's temporary bootstrap token.
* Light-touch :class:`~developer_suite.ui.login_window.LoginWindow`
  construction/interaction tests (``qapp`` from ``pytest-qt``).
"""

from __future__ import annotations

import os
import socket
import threading
import time
from datetime import datetime, timedelta

import pytest
import uvicorn

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from database.database import Database

import server.config as server_config_module
from server.api.app import create_app
from server.config import ServerConfig, get_server_config
from server.database.bootstrap import build_database as build_server_database
from server.models.admin_account import AdminRole
from server.services.admin_auth_service import AdminAuthService

import developer_suite.config as developer_suite_config_module
from developer_suite.admin.auth_client import (
    AdminAuthAccountLockedError,
    AdminAuthClient,
    AdminAuthConnectionError,
    AdminAuthInvalidCredentialsError,
    AdminAuthInvalidTokenError,
)
from developer_suite.admin.session_manager import AdminSessionManager
from developer_suite.config import DeveloperSuiteConfig, get_developer_suite_config
from developer_suite.database.bootstrap import build_database as build_dev_suite_database
from developer_suite.models.admin_session import AdminSessionRecord
from developer_suite.repositories.admin_session_repository import AdminSessionRecordRepository
from developer_suite.ui.login_window import LoginWindow

_STRONG_PASSWORD = "CorrectHorseBattery9!"


@pytest.fixture(autouse=True)
def _reset_developer_suite_config_singleton():
    developer_suite_config_module._config_instance = None
    yield
    developer_suite_config_module._config_instance = None


# ---------------------------------------------------------------------------
# Attendance Server fixtures (same shape as tests/test_phase11_server_auth.py).
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
def auth_client(running_server_url) -> AdminAuthClient:
    return AdminAuthClient(running_server_url)


@pytest.fixture
def session_manager(dev_suite_database: Database, auth_client: AdminAuthClient) -> AdminSessionManager:
    return AdminSessionManager(dev_suite_database, auth_client)


# ---------------------------------------------------------------------------
# AdminSessionRecord + AdminSessionRecordRepository.
# ---------------------------------------------------------------------------


class TestAdminSessionRecordRepository:
    def test_returns_none_when_nothing_saved(self, dev_suite_database: Database) -> None:
        with dev_suite_database.session_scope() as session:
            assert AdminSessionRecordRepository(session).get() is None

    def test_save_then_get_round_trips(self, dev_suite_database: Database) -> None:
        with dev_suite_database.session_scope() as session:
            AdminSessionRecordRepository(session).save(
                username="alice", refresh_token="refresh-abc", remember_me=True
            )
        with dev_suite_database.session_scope() as session:
            record = AdminSessionRecordRepository(session).get()
            assert record is not None
            assert record.username == "alice"
            assert record.refresh_token == "refresh-abc"
            assert record.remember_me is True

    def test_save_overwrites_the_singleton_row(self, dev_suite_database: Database) -> None:
        with dev_suite_database.session_scope() as session:
            AdminSessionRecordRepository(session).save(
                username="alice", refresh_token="first-token", remember_me=True
            )
            AdminSessionRecordRepository(session).save(
                username="bob", refresh_token="second-token", remember_me=True
            )
        with dev_suite_database.session_scope() as session:
            rows = session.query(AdminSessionRecord).all()
            assert len(rows) == 1
            assert rows[0].username == "bob"
            assert rows[0].refresh_token == "second-token"

    def test_clear_removes_the_row(self, dev_suite_database: Database) -> None:
        with dev_suite_database.session_scope() as session:
            AdminSessionRecordRepository(session).save(
                username="alice", refresh_token="refresh-abc", remember_me=True
            )
        with dev_suite_database.session_scope() as session:
            AdminSessionRecordRepository(session).clear()
        with dev_suite_database.session_scope() as session:
            assert AdminSessionRecordRepository(session).get() is None

    def test_clear_is_idempotent_when_nothing_saved(self, dev_suite_database: Database) -> None:
        with dev_suite_database.session_scope() as session:
            AdminSessionRecordRepository(session).clear()  # must not raise

    def test_refresh_token_is_encrypted_at_rest(
        self, dev_suite_database: Database, dev_suite_config: DeveloperSuiteConfig
    ) -> None:
        with dev_suite_database.session_scope() as session:
            AdminSessionRecordRepository(session).save(
                username="alice", refresh_token="super-secret-refresh-token", remember_me=True
            )
        raw_bytes = dev_suite_config.database.sqlite_path.read_bytes()
        assert b"super-secret-refresh-token" not in raw_bytes


# ---------------------------------------------------------------------------
# AdminAuthClient, against a real running server.
# ---------------------------------------------------------------------------


class TestAdminAuthClient:
    def test_login_success(self, auth_client: AdminAuthClient, auth_service: AdminAuthService) -> None:
        auth_service.create_account(username="alice", password=_STRONG_PASSWORD)
        result = auth_client.login("alice", _STRONG_PASSWORD)
        assert result.account.username == "alice"
        assert "." in result.refresh_token

    def test_login_wrong_password_raises(
        self, auth_client: AdminAuthClient, auth_service: AdminAuthService
    ) -> None:
        auth_service.create_account(username="bob", password=_STRONG_PASSWORD)
        with pytest.raises(AdminAuthInvalidCredentialsError):
            auth_client.login("bob", "wrong-password")

    def test_login_locked_account_raises(
        self, auth_client: AdminAuthClient, auth_service: AdminAuthService, server_config: ServerConfig
    ) -> None:
        auth_service.create_account(username="carol", password=_STRONG_PASSWORD)
        for _ in range(server_config.security.max_login_attempts):
            try:
                auth_client.login("carol", "wrong-password")
            except AdminAuthInvalidCredentialsError:
                pass
        with pytest.raises(AdminAuthAccountLockedError):
            auth_client.login("carol", _STRONG_PASSWORD)

    def test_login_connection_error_when_unreachable(self) -> None:
        client = AdminAuthClient("http://127.0.0.1:1", timeout=1.0)
        with pytest.raises(AdminAuthConnectionError):
            client.login("someone", "whatever")

    def test_refresh_rotates_and_old_token_stops_working(
        self, auth_client: AdminAuthClient, auth_service: AdminAuthService
    ) -> None:
        auth_service.create_account(username="dave", password=_STRONG_PASSWORD)
        first = auth_client.login("dave", _STRONG_PASSWORD)
        second = auth_client.refresh(first.refresh_token)
        assert second.refresh_token != first.refresh_token
        with pytest.raises(AdminAuthInvalidTokenError):
            auth_client.refresh(first.refresh_token)

    def test_logout_then_refresh_fails(
        self, auth_client: AdminAuthClient, auth_service: AdminAuthService
    ) -> None:
        auth_service.create_account(username="erin", password=_STRONG_PASSWORD)
        result = auth_client.login("erin", _STRONG_PASSWORD)
        auth_client.logout(result.refresh_token)  # must not raise
        with pytest.raises(AdminAuthInvalidTokenError):
            auth_client.refresh(result.refresh_token)

    def test_change_password_then_login_with_new_password(
        self, auth_client: AdminAuthClient, auth_service: AdminAuthService
    ) -> None:
        auth_service.create_account(username="frank", password=_STRONG_PASSWORD)
        result = auth_client.login("frank", _STRONG_PASSWORD)
        auth_client.change_password(
            result.access_token, current_password=_STRONG_PASSWORD, new_password="AnotherStrongPass1?"
        )
        with pytest.raises(AdminAuthInvalidCredentialsError):
            auth_client.login("frank", _STRONG_PASSWORD)
        auth_client.login("frank", "AnotherStrongPass1?")


# ---------------------------------------------------------------------------
# AdminSessionManager: the real AdminTokenProvider.
# ---------------------------------------------------------------------------


class TestAdminSessionManager:
    def test_login_populates_in_memory_session(
        self, session_manager: AdminSessionManager, auth_service: AdminAuthService
    ) -> None:
        auth_service.create_account(username="grace", password=_STRONG_PASSWORD)
        account = session_manager.login("grace", _STRONG_PASSWORD)
        assert account.username == "grace"
        assert session_manager.is_authenticated
        assert session_manager.current_account.username == "grace"
        assert session_manager.get_token() is not None

    def test_login_without_remember_me_persists_nothing(
        self, session_manager: AdminSessionManager, auth_service: AdminAuthService, dev_suite_database: Database
    ) -> None:
        auth_service.create_account(username="henry", password=_STRONG_PASSWORD)
        session_manager.login("henry", _STRONG_PASSWORD, remember_me=False)
        with dev_suite_database.session_scope() as session:
            assert AdminSessionRecordRepository(session).get() is None

    def test_login_with_remember_me_persists_encrypted_record(
        self, session_manager: AdminSessionManager, auth_service: AdminAuthService, dev_suite_database: Database
    ) -> None:
        auth_service.create_account(username="ivy", password=_STRONG_PASSWORD)
        session_manager.login("ivy", _STRONG_PASSWORD, remember_me=True)
        with dev_suite_database.session_scope() as session:
            record = AdminSessionRecordRepository(session).get()
            assert record is not None
            assert record.username == "ivy"
            assert record.remember_me is True

    def test_login_failure_leaves_session_unauthenticated(
        self, session_manager: AdminSessionManager, auth_service: AdminAuthService
    ) -> None:
        auth_service.create_account(username="jack", password=_STRONG_PASSWORD)
        with pytest.raises(AdminAuthInvalidCredentialsError):
            session_manager.login("jack", "wrong-password")
        assert not session_manager.is_authenticated
        assert session_manager.get_token() is None

    def test_get_token_transparently_refreshes_when_near_expiry(
        self, session_manager: AdminSessionManager, auth_service: AdminAuthService
    ) -> None:
        auth_service.create_account(username="karen", password=_STRONG_PASSWORD)
        session_manager.login("karen", _STRONG_PASSWORD)
        original_token = session_manager.get_token()

        # Force the access token to look expired, without waiting out
        # the real (480-minute default) lifetime.
        session_manager._access_token_expires_at = datetime.now() - timedelta(seconds=1)
        refreshed_token = session_manager.get_token()

        assert refreshed_token is not None
        assert refreshed_token != original_token

    def test_get_token_clears_session_when_refresh_is_rejected(
        self, session_manager: AdminSessionManager, auth_service: AdminAuthService
    ) -> None:
        auth_service.create_account(username="liam", password=_STRONG_PASSWORD)
        session_manager.login("liam", _STRONG_PASSWORD)

        session_manager._refresh_token = "garbage.not-a-real-token"
        session_manager._access_token_expires_at = datetime.now() - timedelta(seconds=1)

        assert session_manager.get_token() is None
        assert not session_manager.is_authenticated

    def test_try_auto_login_with_no_stored_session_returns_false(
        self, session_manager: AdminSessionManager
    ) -> None:
        assert session_manager.try_auto_login() is False
        assert not session_manager.is_authenticated

    def test_try_auto_login_resumes_a_remembered_session(
        self, dev_suite_database: Database, auth_client: AdminAuthClient, auth_service: AdminAuthService
    ) -> None:
        auth_service.create_account(username="maya", password=_STRONG_PASSWORD)
        first_manager = AdminSessionManager(dev_suite_database, auth_client)
        first_manager.login("maya", _STRONG_PASSWORD, remember_me=True)

        # Simulate a fresh application launch: a brand-new manager
        # instance, sharing only the persisted database.
        second_manager = AdminSessionManager(dev_suite_database, auth_client)
        assert second_manager.try_auto_login() is True
        assert second_manager.is_authenticated
        assert second_manager.current_account.username == "maya"

    def test_try_auto_login_does_not_resume_a_session_that_was_not_remembered(
        self, dev_suite_database: Database, auth_client: AdminAuthClient, auth_service: AdminAuthService
    ) -> None:
        auth_service.create_account(username="noah", password=_STRONG_PASSWORD)
        first_manager = AdminSessionManager(dev_suite_database, auth_client)
        first_manager.login("noah", _STRONG_PASSWORD, remember_me=False)

        second_manager = AdminSessionManager(dev_suite_database, auth_client)
        assert second_manager.try_auto_login() is False

    def test_try_auto_login_clears_a_revoked_stored_session(
        self, dev_suite_database: Database, auth_client: AdminAuthClient, auth_service: AdminAuthService
    ) -> None:
        auth_service.create_account(username="olivia", password=_STRONG_PASSWORD)
        first_manager = AdminSessionManager(dev_suite_database, auth_client)
        first_manager.login("olivia", _STRONG_PASSWORD, remember_me=True)
        first_manager.logout()  # revokes the refresh token server-side and clears the local record

        second_manager = AdminSessionManager(dev_suite_database, auth_client)
        assert second_manager.try_auto_login() is False
        with dev_suite_database.session_scope() as session:
            assert AdminSessionRecordRepository(session).get() is None

    def test_logout_clears_in_memory_and_persisted_state(
        self, session_manager: AdminSessionManager, auth_service: AdminAuthService, dev_suite_database: Database
    ) -> None:
        auth_service.create_account(username="peter", password=_STRONG_PASSWORD)
        session_manager.login("peter", _STRONG_PASSWORD, remember_me=True)

        session_manager.logout()

        assert not session_manager.is_authenticated
        assert session_manager.get_token() is None
        with dev_suite_database.session_scope() as session:
            assert AdminSessionRecordRepository(session).get() is None

    def test_logout_when_never_logged_in_does_not_raise(self, session_manager: AdminSessionManager) -> None:
        session_manager.logout()
        assert not session_manager.is_authenticated

    def test_second_login_without_remember_me_clears_a_previously_remembered_session(
        self, session_manager: AdminSessionManager, auth_service: AdminAuthService, dev_suite_database: Database
    ) -> None:
        auth_service.create_account(username="quinn", password=_STRONG_PASSWORD)
        session_manager.login("quinn", _STRONG_PASSWORD, remember_me=True)
        session_manager.login("quinn", _STRONG_PASSWORD, remember_me=False)
        with dev_suite_database.session_scope() as session:
            assert AdminSessionRecordRepository(session).get() is None


# ---------------------------------------------------------------------------
# LoginWindow construction/interaction.
# ---------------------------------------------------------------------------


class TestLoginWindow:
    def test_construction_shows_no_error(self, qapp, session_manager: AdminSessionManager) -> None:
        window = LoginWindow(session_manager)
        assert not window.error_label.isVisible()

    def test_empty_fields_show_inline_error(self, qapp, session_manager: AdminSessionManager) -> None:
        window = LoginWindow(session_manager)
        window.show()
        window._attempt_login()
        assert window.error_label.isVisible()

    def test_successful_login_emits_signal_and_clears_password(
        self, qapp, session_manager: AdminSessionManager, auth_service: AdminAuthService
    ) -> None:
        auth_service.create_account(username="rachel", password=_STRONG_PASSWORD)
        window = LoginWindow(session_manager)
        window.username_edit.setText("rachel")
        window.password_edit.setText(_STRONG_PASSWORD)

        received = []
        window.login_successful.connect(lambda: received.append(True))
        window._attempt_login()

        assert received == [True]
        assert window.password_edit.text() == ""
        assert not window.error_label.isVisible()
        assert session_manager.is_authenticated

    def test_wrong_password_shows_inline_error_and_does_not_emit(
        self, qapp, session_manager: AdminSessionManager, auth_service: AdminAuthService
    ) -> None:
        auth_service.create_account(username="sam", password=_STRONG_PASSWORD)
        window = LoginWindow(session_manager)
        window.show()
        window.username_edit.setText("sam")
        window.password_edit.setText("wrong-password")

        received = []
        window.login_successful.connect(lambda: received.append(True))
        window._attempt_login()

        assert received == []
        assert window.error_label.isVisible()
        assert not session_manager.is_authenticated

    def test_remember_me_checkbox_defaults_unchecked(self, qapp, session_manager: AdminSessionManager) -> None:
        window = LoginWindow(session_manager)
        assert window.remember_me_checkbox.isChecked() is False

    def test_toggle_password_visibility(self, qapp, session_manager: AdminSessionManager) -> None:
        from PySide6.QtWidgets import QLineEdit

        window = LoginWindow(session_manager)
        assert window.password_edit.echoMode() == QLineEdit.Password
        window._toggle_password_visibility()
        assert window.password_edit.echoMode() == QLineEdit.Normal

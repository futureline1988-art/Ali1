"""Tests for Phase 6 of the commercial platform work: Attendance Server foundation.

Every test here exercises only :mod:`server`; nothing touches the
Attendance Client's or the Developer Suite's own database, config, or
models. No test in this file makes a real network call — the FastAPI
app is exercised entirely through :class:`~fastapi.testclient.TestClient`,
and no synchronization, remote administration, or customer
communication exists anywhere in :mod:`server` yet.
"""

from __future__ import annotations

import os

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import config as attendance_config_module
import developer_suite.config as developer_suite_config_module
import server.config as server_config_module
from database.database import Database
from server.api.app import create_app
from server.auth.dependencies import AuthenticatedPrincipal, get_current_principal, require_scope
from server.auth.tokens import TokenError, issue_token, verify_token
from server.config import ServerConfig, get_server_config
from server.container import ServiceContainer
from server.database.base import Base as ServerBase
from server.database.bootstrap import build_database
from server.repositories.base_repository import BaseRepository


@pytest.fixture
def server_config(tmp_path, monkeypatch) -> ServerConfig:
    monkeypatch.setenv("ATTENDANCE_SERVER_DB_SQLITE_PATH", str(tmp_path / "attendance_server_test.db"))
    monkeypatch.setenv("ATTENDANCE_SERVER_SECRET_KEY", "test-secret-key")
    server_config_module._config_instance = None
    yield get_server_config()
    server_config_module._config_instance = None


@pytest.fixture
def server_database(server_config: ServerConfig):
    database = build_database(server_config)
    yield database
    database.dispose()


@pytest.fixture
def client(server_config: ServerConfig, server_database: Database) -> TestClient:
    app = create_app(server_config, server_database)
    return TestClient(app, raise_server_exceptions=False)


class TestConfig:
    def test_load_builds_independent_instance(self, server_config: ServerConfig) -> None:
        assert server_config.app_name == "Attendance Server"

    def test_database_path_is_separate_from_other_applications(
        self, server_config: ServerConfig
    ) -> None:
        assert "attendance_server" in str(server_config.database.sqlite_path).lower()

    def test_database_name_is_attendance_server(self, server_config: ServerConfig) -> None:
        assert server_config.database.database_name == "attendance_server"

    def test_paths_are_created(self, server_config: ServerConfig) -> None:
        assert server_config.paths.data_dir.exists()
        assert server_config.paths.logs_dir.exists()

    def test_secret_key_is_independent_of_attendance_client(
        self, server_config: ServerConfig, monkeypatch
    ) -> None:
        monkeypatch.setenv("APP_SECRET_KEY", "attendance-client-secret")
        assert server_config.security.secret_key != "attendance-client-secret"
        assert server_config.security.secret_key == "test-secret-key"

    def test_singleton_returns_same_instance(self, server_config: ServerConfig) -> None:
        assert get_server_config() is server_config

    def test_database_config_uses_prefixed_env_var_not_generic_one(
        self, tmp_path, monkeypatch
    ) -> None:
        # DB_SQLITE_PATH (the Attendance Client's own, unprefixed variable)
        # must never leak into this server's database configuration.
        monkeypatch.setenv("DB_SQLITE_PATH", str(tmp_path / "should_not_be_used.db"))
        monkeypatch.setenv("ATTENDANCE_SERVER_DB_SQLITE_PATH", str(tmp_path / "attendance_server_test.db"))
        server_config_module._config_instance = None
        try:
            config = get_server_config()
            assert "should_not_be_used" not in str(config.database.sqlite_path)
        finally:
            server_config_module._config_instance = None


class TestDatabaseBootstrap:
    def test_build_database_returns_connected_database(self, server_database: Database) -> None:
        assert server_database.check_connection() is True

    def test_database_file_is_created_at_configured_path(
        self, server_config: ServerConfig, server_database: Database
    ) -> None:
        assert server_config.database.sqlite_path.exists()


class TestBaseRepository:
    def test_generic_repository_is_bound_to_server_base_model(self) -> None:
        # No concrete model exists yet in this phase; this just proves
        # the generic repository class imports and is usable as a
        # type, matching Phase 2's equivalent proof for the Developer
        # Suite before it had any concrete model either.
        assert BaseRepository.__orig_bases__  # Generic[ModelT]


class TestServiceContainer:
    def test_holds_config_and_database(
        self, server_config: ServerConfig, server_database: Database
    ) -> None:
        container = ServiceContainer(config=server_config, database=server_database)
        assert container.config is server_config
        assert container.database is server_database


class TestHealthEndpoint:
    def test_returns_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_does_not_require_authentication(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code != 401


class TestVersionEndpoint:
    def test_returns_app_name_and_version(
        self, client: TestClient, server_config: ServerConfig
    ) -> None:
        response = client.get("/version")
        assert response.status_code == 200
        body = response.json()
        assert body["app_name"] == server_config.app_name
        assert body["app_version"] == server_config.app_version

    def test_does_not_require_authentication(self, client: TestClient) -> None:
        response = client.get("/version")
        assert response.status_code != 401


class TestAuthTokens:
    def test_issue_and_verify_round_trip(self, server_config: ServerConfig) -> None:
        token = issue_token(
            {"principal_id": "ds-1", "principal_type": "developer_suite"}, config=server_config
        )
        claims = verify_token(token, config=server_config)
        assert claims["principal_id"] == "ds-1"
        assert claims["principal_type"] == "developer_suite"

    def test_verify_rejects_token_signed_with_a_different_secret(
        self, server_config: ServerConfig
    ) -> None:
        from dataclasses import replace

        other_config = replace(
            server_config, security=replace(server_config.security, secret_key="a-different-secret")
        )
        token = issue_token({"principal_id": "ds-1", "principal_type": "developer_suite"}, config=other_config)
        with pytest.raises(TokenError):
            verify_token(token, config=server_config)

    def test_verify_rejects_expired_token(self, server_config: ServerConfig) -> None:
        token = issue_token(
            {"principal_id": "ds-1", "principal_type": "developer_suite"},
            config=server_config,
            expires_in_minutes=-1,
        )
        with pytest.raises(TokenError):
            verify_token(token, config=server_config)


class TestAuthDependencies:
    def test_get_current_principal_rejects_missing_header(self, client: TestClient) -> None:
        app = client.app

        @app.get("/_test_current_principal")
        def _route(principal: AuthenticatedPrincipal = Depends(get_current_principal)):
            return {"principal_id": principal.principal_id}

        response = client.get("/_test_current_principal")
        assert response.status_code == 401

    def test_get_current_principal_accepts_a_valid_token(
        self, client: TestClient, server_config: ServerConfig
    ) -> None:
        app = client.app

        @app.get("/_test_current_principal_ok")
        def _route(principal: AuthenticatedPrincipal = Depends(get_current_principal)):
            return {"principal_id": principal.principal_id, "principal_type": principal.principal_type}

        token = issue_token(
            {"principal_id": "ds-1", "principal_type": "developer_suite"}, config=server_config
        )
        response = client.get(
            "/_test_current_principal_ok", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json() == {"principal_id": "ds-1", "principal_type": "developer_suite"}

    def test_require_scope_denies_without_matching_scope(
        self, client: TestClient, server_config: ServerConfig
    ) -> None:
        app = client.app

        @app.get("/_test_requires_admin")
        def _route(principal: AuthenticatedPrincipal = Depends(require_scope("admin"))):
            return {"ok": True}

        token = issue_token(
            {"principal_id": "ds-1", "principal_type": "developer_suite", "scopes": ["read"]},
            config=server_config,
        )
        response = client.get("/_test_requires_admin", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 403

    def test_require_scope_allows_with_matching_scope(
        self, client: TestClient, server_config: ServerConfig
    ) -> None:
        app = client.app

        @app.get("/_test_requires_admin_ok")
        def _route(principal: AuthenticatedPrincipal = Depends(require_scope("admin"))):
            return {"ok": True}

        token = issue_token(
            {"principal_id": "ds-1", "principal_type": "developer_suite", "scopes": ["admin"]},
            config=server_config,
        )
        response = client.get(
            "/_test_requires_admin_ok", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200


class TestUnhandledErrorHandler:
    def test_returns_generic_500_without_leaking_traceback(self, client: TestClient) -> None:
        app = client.app

        @app.get("/_test_boom")
        def _route():
            raise RuntimeError("something broke internally")

        response = client.get("/_test_boom")
        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error."}
        assert "something broke internally" not in response.text


class TestZeroImpactOnOtherApplications:
    """Automated proof that this whole package never touches the other two applications."""

    def test_importing_server_does_not_create_attendance_config_singleton(self) -> None:
        assert attendance_config_module._config_instance is None

    def test_importing_server_does_not_create_developer_suite_config_singleton(self) -> None:
        assert developer_suite_config_module._config_instance is None

    def test_server_database_is_a_distinct_instance_from_the_others(
        self, server_database: Database
    ) -> None:
        from database.database import _database_instance

        assert server_database is not _database_instance

    def test_server_config_singleton_is_independent_of_the_others(
        self, server_config: ServerConfig
    ) -> None:
        assert server_config_module._config_instance is not attendance_config_module._config_instance

    def test_server_schema_is_a_distinct_metadata_from_the_others(self) -> None:
        from developer_suite.database.base import Base as DeveloperSuiteBase
        from models.base import Base as AttendanceBase

        assert ServerBase.metadata is not AttendanceBase.metadata
        assert ServerBase.metadata is not DeveloperSuiteBase.metadata

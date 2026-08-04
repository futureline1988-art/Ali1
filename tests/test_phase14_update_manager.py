"""Tests for Phase 14: Remote Update Manager.

Exercises the full, real stack the same way
``tests/test_phase13_remote_configuration.py`` does: a genuine
Attendance Server (real FastAPI app, real ``uvicorn`` socket), a real
Developer Suite database creating/signing/publishing update versions,
and real Attendance Client databases checking, downloading, and
verifying them — nothing mocked at the HTTP layer.

Folds in what would otherwise be a separate server-only endpoint test
file (every admin and device endpoint in
:mod:`server.api.routers.updates` is exercised here through a real
running server, via :class:`~developer_suite.admin.client.AdminApiClient`
and :class:`~updates.client.UpdatesApiClient`) rather than duplicating
a second, narrower test file.

Two Attendance Client installations share one running Attendance
Server, mirroring Phase 13's own A/B device-targeting isolation setup:

* Client "A": the device a version is actually targeted at.
* Client "B": a different installation on the same server, used to
  prove per-device targeting actually excludes it.

Signing uses a throwaway Ed25519 keypair generated for this test run,
never the real, committed :data:`updates.keys.PUBLIC_KEY_PEM` pair —
this test only has the private half of *its own* keypair, and injects
the matching public half directly into
:class:`~updates.checker.UpdateCheckService` (which accepts a
``public_key`` constructor argument specifically so tests can do this
— see that class's own docstring).
"""

from __future__ import annotations

import os
import socket
import threading
import time
import uuid
from base64 import b64encode
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import uvicorn

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APP_ENVIRONMENT", "testing")

from config import DatabaseConfig
from database.database import Database

import server.config as server_config_module
from server.api.app import create_app
from server.auth.tokens import issue_token
from server.config import ServerConfig, get_server_config
from server.database.bootstrap import build_database as build_server_database

import developer_suite.config as developer_suite_config_module
from developer_suite.admin.client import (
    AdminApiClient,
    AdminApiServerError,
    DeviceInfo,
)
from developer_suite.config import DeveloperSuiteConfig, get_developer_suite_config
from developer_suite.database.bootstrap import build_database as build_dev_suite_database
from developer_suite.services.customer_group_service import CustomerGroupService
from developer_suite.services.customer_service import CustomerService
from developer_suite.services.update_manager_service import UpdateManagerService, UpdateManagerServiceError

from licensing.crypto.signing import generate_keypair, save_private_key

from sync.coordinator import ClientSyncCoordinator
from updates.checker import CannotPostponeMandatoryUpdateError, UpdateCheckService, is_newer_version
from updates.checker import _version_key as client_version_key
from server.services.update_service import _version_key as server_version_key


# ---------------------------------------------------------------------------
# Attendance Server fixtures (mirrors tests/test_phase13_remote_configuration.py).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_developer_suite_config_singleton():
    developer_suite_config_module._config_instance = None
    yield
    developer_suite_config_module._config_instance = None


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


class _StaticTokenProvider:
    """Minimal :class:`~developer_suite.admin.token_provider.AdminTokenProvider` for tests."""

    def __init__(self, token: str) -> None:
        self._token = token

    def get_token(self) -> str | None:
        return self._token


@pytest.fixture
def admin_client(running_server_url: str, admin_bearer_token: str) -> AdminApiClient:
    return AdminApiClient(running_server_url, _StaticTokenProvider(admin_bearer_token))


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
def customer_group_service(dev_suite_database: Database) -> CustomerGroupService:
    return CustomerGroupService(dev_suite_database)


@dataclass
class SigningKeypair:
    """A throwaway Ed25519 keypair for one test run (see this module's docstring)."""

    private_key_path: Path
    public_key: object


@pytest.fixture
def signing_keypair(tmp_path) -> SigningKeypair:
    private_key, public_key = generate_keypair()
    private_key_path = tmp_path / "update_signing_private_key.pem"
    save_private_key(private_key, private_key_path)
    return SigningKeypair(private_key_path=private_key_path, public_key=public_key)


@pytest.fixture
def update_manager_service(admin_client: AdminApiClient, signing_keypair: SigningKeypair) -> UpdateManagerService:
    return UpdateManagerService(admin_client, private_key_path=signing_keypair.private_key_path)


class TestUpdateSigningKeyBootstrap:
    """The update-signing key's own auto-bootstrap wiring (see licensing.crypto.signing.ensure_keypair).

    Deliberately standalone, with no real Attendance Server involved
    (``admin_client=None`` is fine — key bootstrap never touches it) —
    mirrors ``tests/test_developer_suite_phase4.py``'s equivalent
    coverage for the license-signing key one-for-one.
    """

    def test_load_private_key_auto_creates_a_missing_key(self, tmp_path) -> None:
        key_path = tmp_path / "keys" / "update_signing_private_key.pem"
        assert not key_path.exists()
        service = UpdateManagerService(None, private_key_path=key_path)  # type: ignore[arg-type]

        private_key = service._load_private_key()

        assert key_path.exists()
        signature = private_key.sign(b"payload")
        private_key.public_key().verify(signature, b"payload")

    def test_load_private_key_also_writes_the_public_key(self, tmp_path) -> None:
        key_path = tmp_path / "update_signing_private_key.pem"
        public_key_path = tmp_path / "update_signing_public_key.pem"
        service = UpdateManagerService(  # type: ignore[arg-type]
            None, private_key_path=key_path, public_key_path=public_key_path
        )

        service._load_private_key()

        assert public_key_path.exists()
        assert b"BEGIN PUBLIC KEY" in public_key_path.read_bytes()

    def test_load_private_key_never_overwrites_an_existing_key(self, tmp_path) -> None:
        key_path = tmp_path / "update_signing_private_key.pem"
        private_key, _ = generate_keypair()
        save_private_key(private_key, key_path)
        original_bytes = key_path.read_bytes()
        service = UpdateManagerService(None, private_key_path=key_path)  # type: ignore[arg-type]

        service._load_private_key()

        assert key_path.read_bytes() == original_bytes


# ---------------------------------------------------------------------------
# Attendance Client fixtures.
# ---------------------------------------------------------------------------


@dataclass
class ClientInstallation:
    """One enrolled Attendance Client installation used by a test."""

    database: Database
    device_public_id: str
    checker: UpdateCheckService
    downloads_dir: Path


def _standalone_attendance_database(tmp_path, name: str) -> Database:
    db_config = DatabaseConfig(sqlite_path=tmp_path / f"{name}.db")
    database = Database(db_config)
    database.initialize()
    return database


def _make_client(
    tmp_path,
    name: str,
    server_url: str,
    admin_bearer_token: str,
    *,
    signing_keypair: SigningKeypair,
    package_type: str = "setup",
    current_version: str = "1.0.0",
) -> ClientInstallation:
    database = _standalone_attendance_database(tmp_path, name)
    coordinator = ClientSyncCoordinator(database, server_url)
    coordinator.enroll(admin_bearer_token=admin_bearer_token, name=name)
    with database.session_scope() as session:
        from repositories.sync_repository import ClientSyncCredentialRepository

        device_public_id = ClientSyncCredentialRepository(session).get().device_public_id

    downloads_dir = tmp_path / f"{name}_downloads"
    checker = UpdateCheckService(
        database,
        server_url,
        current_version=current_version,
        package_type=package_type,
        downloads_dir=downloads_dir,
        public_key=signing_keypair.public_key,
    )
    return ClientInstallation(
        database=database, device_public_id=device_public_id, checker=checker, downloads_dir=downloads_dir
    )


@pytest.fixture
def client_a(tmp_path, running_server_url, admin_bearer_token, signing_keypair) -> ClientInstallation:
    installation = _make_client(
        tmp_path, "client_a", running_server_url, admin_bearer_token, signing_keypair=signing_keypair
    )
    yield installation
    installation.database.dispose()


@pytest.fixture
def client_b(tmp_path, running_server_url, admin_bearer_token, signing_keypair) -> ClientInstallation:
    installation = _make_client(
        tmp_path, "client_b", running_server_url, admin_bearer_token, signing_keypair=signing_keypair
    )
    yield installation
    installation.database.dispose()


def _publish_simple_version(
    update_manager_service: UpdateManagerService,
    tmp_path: Path,
    *,
    version: str = "2.0.0",
    update_type: str = "recommended",
    package_bytes: bytes = b"fake installer bytes " * 100,
    target_device_public_ids: list[str] | None = None,
):
    """Create, upload a setup package for, target, and publish one version. Returns the version info."""
    created = update_manager_service.create_version(
        version=version, release_notes="Bug fixes.", min_supported_version=None, update_type=update_type
    )
    package_path = tmp_path / f"installer_{version}.bin"
    package_path.write_bytes(package_bytes)
    update_manager_service.upload_package(created.id, package_type="setup", file_path=package_path)
    if target_device_public_ids is None:
        update_manager_service.set_targets_all(created.id)
    else:
        update_manager_service.set_targets_devices(created.id, device_public_ids=target_device_public_ids)
    update_manager_service.publish(created.id)
    return created


# ---------------------------------------------------------------------------
# Version comparison: client and server must agree byte-for-byte.
# ---------------------------------------------------------------------------


class TestVersionComparison:
    @pytest.mark.parametrize(
        "candidate,current,expected",
        [
            ("1.10.0", "1.9.0", True),
            ("1.9.0", "1.10.0", False),
            ("2.0.0", "2.0.0", False),
            ("1.0.1", "1.0.0", True),
            # A trailing non-numeric segment parses as an extra "0" component,
            # so "1.0.0-beta" (1, 0, 0, 0) compares greater than "1.0.0" (1, 0, 0)
            # under this algorithm - documented here rather than silently relied on.
            ("1.0.0-beta", "1.0.0", True),
        ],
    )
    def test_is_newer_version(self, candidate, current, expected) -> None:
        assert is_newer_version(candidate, current) is expected

    def test_client_and_server_version_key_algorithms_agree(self) -> None:
        for version in ("1.0.0", "1.9.0", "1.10.0", "2.0.0-rc1", "0.0.1"):
            assert client_version_key(version) == server_version_key(version)


# ---------------------------------------------------------------------------
# Server admin endpoints: version lifecycle, packages, targeting.
# ---------------------------------------------------------------------------


class TestAdminVersionLifecycle:
    def test_create_duplicate_version_string_is_rejected(self, update_manager_service) -> None:
        update_manager_service.create_version(
            version="3.0.0", release_notes=None, min_supported_version=None, update_type="optional"
        )
        with pytest.raises(UpdateManagerServiceError):
            update_manager_service.create_version(
                version="3.0.0", release_notes=None, min_supported_version=None, update_type="optional"
            )

    def test_publish_without_a_package_is_rejected(self, update_manager_service) -> None:
        created = update_manager_service.create_version(
            version="3.1.0", release_notes=None, min_supported_version=None, update_type="optional"
        )
        with pytest.raises(UpdateManagerServiceError):
            update_manager_service.publish(created.id)

    def test_upload_with_tampered_checksum_is_rejected(self, admin_client, update_manager_service, tmp_path) -> None:
        created = update_manager_service.create_version(
            version="3.2.0", release_notes=None, min_supported_version=None, update_type="optional"
        )
        with pytest.raises(AdminApiServerError):
            admin_client.upload_update_package(
                created.id,
                package_type="setup",
                file_bytes=b"real bytes",
                checksum_sha256="0" * 64,
                signature_base64=b64encode(b"whatever").decode("ascii"),
                original_filename="setup.exe",
            )

    def test_full_lifecycle_publish_schedule_disable_rollback(self, update_manager_service, tmp_path) -> None:
        version = _publish_simple_version(update_manager_service, tmp_path, version="3.3.0")
        assert version.version == "3.3.0"

        detail = update_manager_service.get_version_detail(version.id)
        assert detail["version"].publish_status == "published"
        assert len(detail["packages"]) == 1
        assert any(event["action"] == "published" for event in detail["audit_events"])

        disabled = update_manager_service.disable(version.id)
        assert disabled.publish_status == "disabled"

        rolled_back = update_manager_service.rollback(version.id, reason="Bad build.")
        assert rolled_back.publish_status == "rolled_back"
        detail_after_rollback = update_manager_service.get_version_detail(version.id)
        assert len(detail_after_rollback["rollbacks"]) == 1
        assert detail_after_rollback["rollbacks"][0]["reason"] == "Bad build."

    def test_schedule_becomes_live_only_once_due(self, update_manager_service, tmp_path, client_a) -> None:
        created = update_manager_service.create_version(
            version="3.4.0", release_notes=None, min_supported_version=None, update_type="optional"
        )
        package_path = tmp_path / "installer_3.4.0.bin"
        package_path.write_bytes(b"future bytes")
        update_manager_service.upload_package(created.id, package_type="setup", file_path=package_path)
        update_manager_service.set_targets_all(created.id)
        update_manager_service.schedule(created.id, scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1))

        # Not due yet - the client sees nothing.
        assert client_a.checker.check_for_update() is None

        # Reschedule into the past (simulating "time has passed") - now live.
        update_manager_service.schedule(created.id, scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        state = client_a.checker.check_for_update()
        assert state is not None
        assert state.version == "3.4.0"


# ---------------------------------------------------------------------------
# Full lifecycle: publish -> check -> download -> verify, with device targeting.
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    def test_targeted_update_is_discovered_downloaded_and_verified_only_by_its_target(
        self, update_manager_service, tmp_path, client_a, client_b
    ) -> None:
        package_bytes = os.urandom(500_000)
        version = _publish_simple_version(
            update_manager_service,
            tmp_path,
            version="4.0.0",
            package_bytes=package_bytes,
            target_device_public_ids=[client_a.device_public_id],
        )

        state_a = client_a.checker.check_for_update()
        assert state_a is not None
        assert state_a.version == "4.0.0"
        assert state_a.status == "discovered"

        state_b = client_b.checker.check_for_update()
        assert state_b is None

        ok = client_a.checker.download_and_verify(version.id)
        assert ok is True

        with client_a.database.session_scope() as session:
            from repositories.update_state_repository import ClientUpdateStateRepository

            row = ClientUpdateStateRepository(session).get_by_version_id(version.id)
            assert row.status == "verified"
            local_path = Path(row.local_file_path)

        assert local_path.exists()
        assert local_path.read_bytes() == package_bytes
        assert not local_path.with_name(local_path.name + ".partial").exists()

    def test_repeat_check_does_not_regress_an_already_verified_status(
        self, update_manager_service, tmp_path, client_a
    ) -> None:
        version = _publish_simple_version(update_manager_service, tmp_path, version="4.1.0")
        client_a.checker.check_for_update()
        assert client_a.checker.download_and_verify(version.id) is True

        state = client_a.checker.check_for_update()
        assert state.status == "verified"

    def test_resume_after_a_truncated_partial_download_produces_a_byte_identical_file(
        self, update_manager_service, tmp_path, client_a
    ) -> None:
        package_bytes = os.urandom(300_000)
        version = _publish_simple_version(update_manager_service, tmp_path, version="4.2.0", package_bytes=package_bytes)
        client_a.checker.check_for_update()

        # Simulate an interrupted download: a real, valid, truncated .partial
        # file already sitting where the next download would write.
        dest_path = client_a.downloads_dir / "update_4.2.0_setup.bin"
        partial_path = dest_path.with_name(dest_path.name + ".partial")
        partial_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path.write_bytes(package_bytes[:100_000])

        assert client_a.checker.download_and_verify(version.id) is True
        assert dest_path.read_bytes() == package_bytes


# ---------------------------------------------------------------------------
# Corrupted / tampered packages must never verify.
# ---------------------------------------------------------------------------


class TestNeverInstallCorruptedPackages:
    def test_checksum_mismatch_fails_and_deletes_the_file(self, update_manager_service, tmp_path, client_a) -> None:
        version = _publish_simple_version(update_manager_service, tmp_path, version="5.0.0")
        client_a.checker.check_for_update()

        with client_a.database.session_scope() as session:
            from repositories.update_state_repository import ClientUpdateStateRepository

            repo = ClientUpdateStateRepository(session)
            state = repo.get_by_version_id(version.id)
            state.checksum_sha256 = "0" * 64

        ok = client_a.checker.download_and_verify(version.id)
        assert ok is False

        with client_a.database.session_scope() as session:
            from repositories.update_state_repository import ClientUpdateStateRepository

            state = ClientUpdateStateRepository(session).get_by_version_id(version.id)
            assert state.status == "failed"
            assert state.error_message

        downloaded_file = client_a.downloads_dir / "update_5.0.0_setup.bin"
        assert not downloaded_file.exists()

    def test_signature_mismatch_fails_and_deletes_the_file(self, update_manager_service, tmp_path, client_a) -> None:
        version = _publish_simple_version(update_manager_service, tmp_path, version="5.1.0")
        client_a.checker.check_for_update()

        with client_a.database.session_scope() as session:
            from repositories.update_state_repository import ClientUpdateStateRepository

            repo = ClientUpdateStateRepository(session)
            state = repo.get_by_version_id(version.id)
            state.signature_base64 = b64encode(b"not a real signature at all").decode("ascii")

        ok = client_a.checker.download_and_verify(version.id)
        assert ok is False

        downloaded_file = client_a.downloads_dir / "update_5.1.0_setup.bin"
        assert not downloaded_file.exists()


# ---------------------------------------------------------------------------
# Mandatory updates: informed clearly, postponable only when not mandatory.
# ---------------------------------------------------------------------------


class TestMandatoryUpdates:
    def test_mandatory_update_cannot_be_postponed(self, update_manager_service, tmp_path, client_a) -> None:
        version = _publish_simple_version(update_manager_service, tmp_path, version="6.0.0", update_type="mandatory")
        state = client_a.checker.check_for_update()
        assert state.update_type == "mandatory"
        assert client_a.checker.is_postponable(state) is False

        with pytest.raises(CannotPostponeMandatoryUpdateError):
            client_a.checker.postpone(version.id, until=datetime.now(timezone.utc) + timedelta(hours=24))

    def test_recommended_update_can_be_postponed(self, update_manager_service, tmp_path, client_a) -> None:
        version = _publish_simple_version(update_manager_service, tmp_path, version="6.1.0", update_type="recommended")
        state = client_a.checker.check_for_update()
        assert client_a.checker.is_postponable(state) is True

        until = datetime.now(timezone.utc) + timedelta(hours=24)
        client_a.checker.postpone(version.id, until=until)

        with client_a.database.session_scope() as session:
            from repositories.update_state_repository import ClientUpdateStateRepository

            row = ClientUpdateStateRepository(session).get_by_version_id(version.id)
            assert row.status == "postponed"


# ---------------------------------------------------------------------------
# Device status reporting -> Developer Dashboard statistics.
# ---------------------------------------------------------------------------


class TestDashboardStats:
    def test_installed_and_failed_reports_are_aggregated(
        self, update_manager_service, tmp_path, client_a, client_b
    ) -> None:
        version = _publish_simple_version(update_manager_service, tmp_path, version="7.0.0")

        client_a.checker.check_for_update()
        assert client_a.checker.download_and_verify(version.id) is True
        # Simulate the (out-of-Phase-14-scope) installer step reporting success.
        _report_status(client_a, version.id, status="installed")

        client_b.checker.check_for_update()
        _report_status(client_b, version.id, status="failed", error_message="disk full")

        stats = update_manager_service.get_stats()
        assert stats.successful_count >= 1
        assert stats.failed_count >= 1
        assert stats.companies_per_version.get("7.0.0", 0) >= 1
        assert stats.latest_deployed_version == "7.0.0"


def _report_status(
    client: ClientInstallation,
    update_version_id: int,
    *,
    status: str,
    progress_percent: int = 100,
    error_message: str | None = None,
) -> None:
    """Report a device's update status directly, simulating what an installer step would call.

    Used by dashboard-aggregation tests to produce statuses (like
    ``"installed"``) that nothing in Phase 14's own client code
    reaches on its own - this application never auto-installs (see
    :mod:`updates.checker`'s own docstring).
    """
    from repositories.sync_repository import ClientSyncCredentialRepository
    from updates.client import UpdatesApiClient

    with client.database.session_scope() as session:
        credential = ClientSyncCredentialRepository(session).get()
        server_url = credential.server_url
        device_public_id = credential.device_public_id
        device_api_key = credential.api_key
    api_client = UpdatesApiClient(server_url, device_public_id=device_public_id, device_api_key=device_api_key)
    try:
        api_client.report_status(
            update_version_id=update_version_id,
            status=status,
            progress_percent=progress_percent,
            error_message=error_message,
        )
    finally:
        api_client.close()


# ---------------------------------------------------------------------------
# Customer groups: membership resolves to a suggested device list.
# ---------------------------------------------------------------------------


class TestCustomerGroupsAndTargetingSuggestions:
    def test_group_membership_round_trips(self, customer_group_service, customer_service) -> None:
        acme = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        globex = customer_service.create_customer(company_name="Globex Inc", contact_name="John Roe")
        group = customer_group_service.create_group(name="Enterprise Tier")

        customer_group_service.set_members(group.id, customer_ids=[acme.id, globex.id])
        fetched = customer_group_service.get_group(group.id)
        assert {c.company_name for c in fetched.customers} == {"Acme Co", "Globex Inc"}

        customer_group_service.set_members(group.id, customer_ids=[acme.id])
        fetched = customer_group_service.get_group(group.id)
        assert {c.company_name for c in fetched.customers} == {"Acme Co"}

    def test_suggest_devices_for_customers_is_a_best_effort_name_match(
        self, update_manager_service, customer_service
    ) -> None:
        acme = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        devices = [
            DeviceInfo(
                public_id=str(uuid.uuid4()),
                name="Acme Co - Front Desk",
                device_type="attendance_client",
                is_active=True,
                last_seen_at=None,
                created_at=datetime.now(timezone.utc),
            ),
            DeviceInfo(
                public_id=str(uuid.uuid4()),
                name="Unrelated Company Kiosk",
                device_type="attendance_client",
                is_active=True,
                last_seen_at=None,
                created_at=datetime.now(timezone.utc),
            ),
        ]
        suggested = update_manager_service.suggest_devices_for_customers([acme], registered_devices=devices)
        assert suggested == [devices[0].public_id]


# ---------------------------------------------------------------------------
# Isolation: nothing added by Phase 14 leaks across application boundaries.
# ---------------------------------------------------------------------------


class TestZeroImpactOnOtherApplications:
    def test_client_update_state_table_lives_only_in_the_attendance_client_schema(self) -> None:
        from developer_suite.database.base import Base as DeveloperSuiteBase
        from models.base import Base as AttendanceBase
        from server.database.base import Base as ServerBase

        assert "client_update_state" in AttendanceBase.metadata.tables
        assert "client_update_state" not in DeveloperSuiteBase.metadata.tables
        assert "client_update_state" not in ServerBase.metadata.tables

    def test_update_tables_live_only_in_the_server_schema(self) -> None:
        from developer_suite.database.base import Base as DeveloperSuiteBase
        from models.base import Base as AttendanceBase
        from server.database.base import Base as ServerBase

        for table_name in (
            "update_versions",
            "update_packages",
            "update_targets",
            "update_rollbacks",
            "device_update_statuses",
            "update_audit_events",
        ):
            assert table_name in ServerBase.metadata.tables
            assert table_name not in AttendanceBase.metadata.tables
            assert table_name not in DeveloperSuiteBase.metadata.tables

    def test_customer_group_table_lives_only_in_the_developer_suite_schema(self) -> None:
        from developer_suite.database.base import Base as DeveloperSuiteBase
        from models.base import Base as AttendanceBase
        from server.database.base import Base as ServerBase

        assert "customer_groups" in DeveloperSuiteBase.metadata.tables
        assert "customer_groups" not in AttendanceBase.metadata.tables
        assert "customer_groups" not in ServerBase.metadata.tables

    def test_updates_package_imports_nothing_from_developer_suite_or_server(self) -> None:
        import ast
        import inspect

        import updates.checker as checker_module
        import updates.client as client_module
        import updates.keys as keys_module
        import updates.protocol as protocol_module
        import updates.verifier as verifier_module

        for module in (protocol_module, keys_module, client_module, verifier_module, checker_module):
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("developer_suite"), module.__name__
                    assert not node.module.startswith("server"), module.__name__
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("developer_suite"), module.__name__
                        assert not alias.name.startswith("server"), module.__name__

    def test_update_signing_key_is_a_different_keypair_from_licensing(self) -> None:
        from licensing.keys import PUBLIC_KEY_PEM as LICENSE_PUBLIC_KEY_PEM
        from updates.keys import PUBLIC_KEY_PEM as UPDATE_PUBLIC_KEY_PEM

        assert LICENSE_PUBLIC_KEY_PEM != UPDATE_PUBLIC_KEY_PEM

    def test_importing_updates_package_does_not_create_developer_suite_config_singleton(self) -> None:
        import updates.checker  # noqa: F401

        assert developer_suite_config_module._config_instance is None

    def test_sync_scheduler_still_works_with_update_checking_disabled(self, tmp_path) -> None:
        """Regression guard: Phase 14's scheduler hook must not affect a plain sync cycle."""
        from database.database import Database as AttendanceDatabase
        from sync.coordinator import ClientSyncCoordinator
        from sync.scheduler import ClientSyncSchedulerService

        database = _standalone_attendance_database(tmp_path, "scheduler_regression")
        coordinator = ClientSyncCoordinator(database, "http://127.0.0.1:1")
        scheduler = ClientSyncSchedulerService(
            coordinator, database, sync_enabled=True, sync_interval_seconds=3600, update_check_service=None
        )
        status = scheduler.sync_now()
        assert status.state.value == "idle"
        database.dispose()

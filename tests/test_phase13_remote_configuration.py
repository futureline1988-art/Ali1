"""Tests for Phase 13: Remote Configuration.

Exercises the full, real stack the same way
``tests/test_phase8_customer_sync.py`` does: a genuine Attendance
Server (real FastAPI app, real ``uvicorn`` socket), a real Developer
Suite database publishing configuration, and a real Attendance Client
database enrolling and pulling it — nothing mocked at the HTTP layer.

Three parties share one running Attendance Server in the full-lifecycle
tests below:

* The Developer Suite (source of truth): builds a bundle, publishes it
  to a specific target device.
* Attendance Client "A" (the actual target): enrolls, pulls, and must
  receive and apply the change.
* Attendance Client "B" (a different installation on the same server):
  enrolls independently and must *not* receive a change addressed to
  A — the device-targeting mechanism
  :mod:`sync.coordinator`'s own docstring documents.
"""

from __future__ import annotations

import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass

import pytest
import uvicorn

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APP_ENVIRONMENT", "testing")

import config as attendance_config_module
from config import DatabaseConfig
from database.database import Database, session_scope

import server.config as server_config_module
from server.api.app import create_app
from server.auth.tokens import issue_token
from server.config import ServerConfig, get_server_config
from server.database.bootstrap import build_database as build_server_database
from server.services.sync_service import SyncService as ServerSyncService

import developer_suite.config as developer_suite_config_module
from developer_suite.config import DeveloperSuiteConfig, DeveloperSuitePaths, get_developer_suite_config
from developer_suite.database.bootstrap import build_database as build_dev_suite_database
from developer_suite.services.configuration_publish_service import (
    ConfigurationPublishService,
    NotAnAdministratorError,
)
from developer_suite.services.configuration_service import ConfigurationService
from developer_suite.services.customer_service import CustomerService
from developer_suite.sync.configuration_sync import ENTITY_TYPE, build_payload, compute_payload_checksum
from developer_suite.sync.coordinator import SyncCoordinator

from models.company import Company
from models.company_settings import CompanySettings
from repositories.company_repository import CompanyRepository
from repositories.company_settings_repository import CompanySettingsRepository
from sync.client import SyncClientError
from sync.coordinator import ClientSyncCoordinator, DeviceNotEnrolledError
from sync.protocol import compute_checksum as client_compute_checksum


# ---------------------------------------------------------------------------
# Attendance Server fixtures (mirrors tests/test_phase8_customer_sync.py's
# own fixtures exactly).
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
def configuration_service(dev_suite_database: Database) -> ConfigurationService:
    return ConfigurationService(dev_suite_database)


@pytest.fixture
def publish_service(dev_suite_database: Database) -> ConfigurationPublishService:
    return ConfigurationPublishService(dev_suite_database)


@pytest.fixture
def dev_suite_pusher(
    dev_suite_database: Database,
    dev_suite_config: DeveloperSuiteConfig,
    running_server_url: str,
    admin_bearer_token: str,
):
    """A callable that delivers every currently-queued outbox entry to the real server.

    :meth:`~developer_suite.services.configuration_publish_service.ConfigurationPublishService.publish`
    only enqueues locally (see that module's own docstring: delivery is
    a side effect of the existing outbox, reused unmodified) — in
    production, :class:`~developer_suite.sync.scheduler.SyncSchedulerService`
    is what periodically calls
    :meth:`~developer_suite.sync.coordinator.SyncCoordinator.push_pending`.
    Tests below call it directly, once per publish, to make that
    delivery step explicit and deterministic.
    """
    import dataclasses

    from developer_suite.sync.client import DeviceType as DevSuiteDeviceType

    coordinator = SyncCoordinator(
        dev_suite_database, dataclasses.replace(dev_suite_config, attendance_server_url=running_server_url)
    )
    coordinator.enroll(
        admin_bearer_token=admin_bearer_token, name="dev_suite_pusher", device_type=DevSuiteDeviceType.DEVELOPER_SUITE
    )

    def _push() -> None:
        coordinator.push_pending()

    return _push


@pytest.fixture
def customer_service(dev_suite_database: Database) -> CustomerService:
    return CustomerService(dev_suite_database)


def _build_bundle(configuration_service: ConfigurationService, *, name: str = "Default Bundle"):
    """Create the five minimal profiles a bundle needs and compose them."""
    theme = configuration_service.create_theme_profile(name=f"{name} Theme")
    print_profile = configuration_service.create_print_profile(name=f"{name} Print")
    policy = configuration_service.create_attendance_policy_profile(name=f"{name} Policy")
    device = configuration_service.create_device_profile(name=f"{name} Device")
    backup = configuration_service.create_backup_profile(name=f"{name} Backup")
    return configuration_service.create_configuration(
        name=name,
        theme_profile_id=theme.id,
        print_profile_id=print_profile.id,
        attendance_policy_profile_id=policy.id,
        device_profile_id=device.id,
        backup_profile_id=backup.id,
    )


# ---------------------------------------------------------------------------
# Attendance Client fixtures.
# ---------------------------------------------------------------------------


@dataclass
class ClientInstallation:
    """One enrolled Attendance Client installation used by a test."""

    database: Database
    coordinator: ClientSyncCoordinator
    device_public_id: str


def _standalone_attendance_database(tmp_path, name: str) -> Database:
    """Build a genuinely separate Attendance Client database for one test.

    Bypasses the process-wide :func:`~database.database.get_database`
    singleton (constructing :class:`~database.database.Database`
    directly), the same way ``test_phase8_customer_sync.py`` bypasses
    the Developer Suite's singleton — two simulated installations need
    two real, independent databases.
    """
    db_config = DatabaseConfig(sqlite_path=tmp_path / f"{name}.db")
    database = Database(db_config)
    database.initialize()
    return database


def _make_client(tmp_path, name: str, server_url: str, admin_bearer_token: str) -> ClientInstallation:
    database = _standalone_attendance_database(tmp_path, name)
    coordinator = ClientSyncCoordinator(database, server_url)
    coordinator.enroll(admin_bearer_token=admin_bearer_token, name=name)
    with database.session_scope() as session:
        from repositories.sync_repository import ClientSyncCredentialRepository

        device_public_id = ClientSyncCredentialRepository(session).get().device_public_id
        company = Company(name=f"{name} Company", is_active=True)
        CompanyRepository(session).add(company)
    return ClientInstallation(database=database, coordinator=coordinator, device_public_id=device_public_id)


@pytest.fixture
def client_a(tmp_path, running_server_url, admin_bearer_token) -> ClientInstallation:
    installation = _make_client(tmp_path, "client_a", running_server_url, admin_bearer_token)
    yield installation
    installation.database.dispose()


@pytest.fixture
def client_b(tmp_path, running_server_url, admin_bearer_token) -> ClientInstallation:
    installation = _make_client(tmp_path, "client_b", running_server_url, admin_bearer_token)
    yield installation
    installation.database.dispose()


# ---------------------------------------------------------------------------
# Payload builder.
# ---------------------------------------------------------------------------


class TestPayloadBuilder:
    def test_build_payload_shape(self, configuration_service, customer_service) -> None:
        bundle = _build_bundle(configuration_service)
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")

        payload = build_payload(customer, bundle)

        assert set(payload.keys()) == {"company", "theme", "print", "attendance_policy", "device", "backup"}
        assert payload["company"]["name"] == "Acme Co"
        assert payload["theme"]["language"] == "ar"
        assert payload["attendance_policy"]["working_days"]

    def test_checksum_is_deterministic_regardless_of_key_order(self) -> None:
        a = {"b": 2, "a": 1}
        b = {"a": 1, "b": 2}
        assert compute_payload_checksum(a) == compute_payload_checksum(b)

    def test_client_checksum_algorithm_matches_developer_suite_and_server(
        self, configuration_service, customer_service, server_database
    ) -> None:
        """The client's independently-replicated checksum must be byte-identical."""
        bundle = _build_bundle(configuration_service)
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        payload = build_payload(customer, bundle)

        dev_suite_checksum = compute_payload_checksum(payload)
        client_checksum = client_compute_checksum(payload)
        server_checksum = ServerSyncService(server_database).compute_checksum(payload)

        assert dev_suite_checksum == client_checksum == server_checksum


# ---------------------------------------------------------------------------
# ConfigurationPublishService: publish, compare, rollback, history.
# ---------------------------------------------------------------------------


class TestConfigurationPublishService:
    def test_publish_requires_a_published_by(self, publish_service, configuration_service, customer_service) -> None:
        bundle = _build_bundle(configuration_service)
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        with pytest.raises(NotAnAdministratorError):
            publish_service.publish(
                bundle.id,
                customer_id=customer.id,
                target_device_public_id=str(uuid.uuid4()),
                published_by="",
            )

    def test_first_publish_is_version_1_with_create_operation_outbox_entry(
        self, dev_suite_database, publish_service, configuration_service, customer_service
    ) -> None:
        bundle = _build_bundle(configuration_service)
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        device_id = str(uuid.uuid4())

        publication = publish_service.publish(
            bundle.id, customer_id=customer.id, target_device_public_id=device_id, published_by="admin"
        )

        assert publication.version == 1
        assert publication.published_by == "admin"

        from developer_suite.models.sync_state import SyncOperation as DevSyncOperation
        from developer_suite.repositories.sync_repository import SyncOutboxRepository

        with dev_suite_database.session_scope() as session:
            entry = SyncOutboxRepository(session).get_by_entity(ENTITY_TYPE, device_id)
            assert entry is not None
            assert entry.operation is DevSyncOperation.CREATE

    def test_compare_pending_changes_shows_only_the_edited_field(
        self, publish_service, configuration_service, customer_service
    ) -> None:
        bundle = _build_bundle(configuration_service)
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        device_id = str(uuid.uuid4())

        publish_service.publish(
            bundle.id, customer_id=customer.id, target_device_public_id=device_id, published_by="admin"
        )
        no_diff = publish_service.compare_pending_changes(
            bundle.id, customer_id=customer.id, target_device_public_id=device_id
        )
        assert no_diff == {}

        theme = configuration_service.list_theme_profiles()[0]
        configuration_service.update_theme_profile(
            theme.id,
            name=theme.name,
            mode=theme.mode,
            primary_color="#FF0000",
            secondary_color=theme.secondary_color,
        )

        diff = publish_service.compare_pending_changes(
            bundle.id, customer_id=customer.id, target_device_public_id=device_id
        )
        assert diff == {"theme.primary_color": ("#1976D2", "#FF0000")}

    def test_publish_after_edit_creates_version_2_and_history_lists_both(
        self, publish_service, configuration_service, customer_service
    ) -> None:
        bundle = _build_bundle(configuration_service)
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        device_id = str(uuid.uuid4())

        publish_service.publish(
            bundle.id, customer_id=customer.id, target_device_public_id=device_id, published_by="admin"
        )
        theme = configuration_service.list_theme_profiles()[0]
        configuration_service.update_theme_profile(
            theme.id, name=theme.name, mode=theme.mode, primary_color="#FF0000", secondary_color=theme.secondary_color
        )
        publish_service.publish(
            bundle.id, customer_id=customer.id, target_device_public_id=device_id, published_by="admin"
        )

        history = publish_service.list_publication_history(device_id)
        assert [p.version for p in history] == [2, 1]

    def test_rollback_creates_a_new_version_and_never_deletes_history(
        self, publish_service, configuration_service, customer_service
    ) -> None:
        bundle = _build_bundle(configuration_service)
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        device_id = str(uuid.uuid4())

        v1 = publish_service.publish(
            bundle.id, customer_id=customer.id, target_device_public_id=device_id, published_by="admin"
        )
        theme = configuration_service.list_theme_profiles()[0]
        configuration_service.update_theme_profile(
            theme.id, name=theme.name, mode=theme.mode, primary_color="#FF0000", secondary_color=theme.secondary_color
        )
        publish_service.publish(
            bundle.id, customer_id=customer.id, target_device_public_id=device_id, published_by="admin"
        )

        rollback = publish_service.rollback(device_id, to_publication_id=v1.id, published_by="admin")

        assert rollback.version == 3
        assert rollback.payload["theme"]["primary_color"] == "#1976D2"
        assert rollback.change_summary == "Rollback to version 1."

        history = publish_service.list_publication_history(device_id)
        assert [p.version for p in history] == [3, 2, 1]

    def test_repeated_publishes_with_no_intervening_push_still_coalesce_as_create(
        self, dev_suite_database, publish_service, configuration_service, customer_service
    ) -> None:
        """Regression guard for the outbox coalescing semantics Phase 8 established."""
        bundle = _build_bundle(configuration_service)
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        device_id = str(uuid.uuid4())

        for _ in range(3):
            publish_service.publish(
                bundle.id, customer_id=customer.id, target_device_public_id=device_id, published_by="admin"
            )

        from developer_suite.models.sync_state import SyncOperation as DevSyncOperation
        from developer_suite.repositories.sync_repository import SyncOutboxRepository

        with dev_suite_database.session_scope() as session:
            entry = SyncOutboxRepository(session).get_by_entity(ENTITY_TYPE, device_id)
            assert entry is not None
            assert entry.operation is DevSyncOperation.CREATE


# ---------------------------------------------------------------------------
# Attendance Client enrollment.
# ---------------------------------------------------------------------------


class TestClientEnrollment:
    def test_enroll_persists_an_encrypted_credential(self, tmp_path, running_server_url, admin_bearer_token) -> None:
        database = _standalone_attendance_database(tmp_path, "enroll_test")
        coordinator = ClientSyncCoordinator(database, running_server_url)

        assert coordinator.is_enrolled() is False
        coordinator.enroll(admin_bearer_token=admin_bearer_token, name="Test Client")
        assert coordinator.is_enrolled() is True

        from repositories.sync_repository import ClientSyncCredentialRepository

        with database.session_scope() as session:
            credential = ClientSyncCredentialRepository(session).get()
            assert credential is not None
            assert uuid.UUID(credential.device_public_id)
            assert credential.api_key

        raw_bytes = database.config.sqlite_path.read_bytes()
        assert credential.api_key.encode("utf-8") not in raw_bytes
        database.dispose()

    def test_pull_and_apply_without_enrollment_raises(self, tmp_path, running_server_url) -> None:
        database = _standalone_attendance_database(tmp_path, "unenrolled")
        coordinator = ClientSyncCoordinator(database, running_server_url)
        with pytest.raises(DeviceNotEnrolledError):
            coordinator.pull_and_apply(ENTITY_TYPE)
        database.dispose()


# ---------------------------------------------------------------------------
# Full lifecycle: publish -> server -> pull -> apply, with device targeting.
# ---------------------------------------------------------------------------


class TestFullLifecyclePublishPullApply:
    def test_published_change_is_applied_only_by_its_target_device(
        self, publish_service, configuration_service, customer_service, dev_suite_pusher, client_a, client_b
    ) -> None:
        bundle = _build_bundle(configuration_service)
        customer = customer_service.create_customer(
            company_name="Acme Co", contact_name="Jane Doe", phone="0770000000", email="acme@example.com"
        )
        publish_service.publish(
            bundle.id,
            customer_id=customer.id,
            target_device_public_id=client_a.device_public_id,
            published_by="admin",
        )
        dev_suite_pusher()

        a_summary = client_a.coordinator.pull_and_apply(ENTITY_TYPE)
        assert a_summary.applied == 1
        assert a_summary.skipped_other_device == 0

        b_summary = client_b.coordinator.pull_and_apply(ENTITY_TYPE)
        assert b_summary.applied == 0
        assert b_summary.skipped_other_device == 1

        with client_a.database.session_scope() as session:
            company = CompanyRepository(session).list_active()[0]
            assert company.name == "Acme Co"
            assert company.phone == "0770000000"
            settings = CompanySettingsRepository(session, company_id=company.id).get_for_company()
            assert settings.remote_config_version == 1
            assert settings.remote_config_checksum

        with client_b.database.session_scope() as session:
            company = CompanyRepository(session).list_active()[0]
            assert company.name == "client_b Company"

    def test_repeat_pull_with_nothing_new_applies_nothing(
        self, publish_service, configuration_service, customer_service, dev_suite_pusher, client_a
    ) -> None:
        bundle = _build_bundle(configuration_service)
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        publish_service.publish(
            bundle.id, customer_id=customer.id, target_device_public_id=client_a.device_public_id, published_by="admin"
        )
        dev_suite_pusher()

        first = client_a.coordinator.pull_and_apply(ENTITY_TYPE)
        assert first.applied == 1

        second = client_a.coordinator.pull_and_apply(ENTITY_TYPE)
        assert second.applied == 0
        assert second.next_cursor == first.next_cursor

    def test_language_change_sets_restart_required_but_unrelated_change_does_not(
        self, publish_service, configuration_service, customer_service, dev_suite_pusher, client_a
    ) -> None:
        bundle = _build_bundle(configuration_service)
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")

        # The very first apply legitimately requires a restart: it moves
        # theme_font_family from unset (None) to a real value for the
        # first time (see sync.configuration_apply's own contract) —
        # acknowledge and clear it here, exactly as main.py's one-time
        # notice does after showing it to the user.
        publish_service.publish(
            bundle.id, customer_id=customer.id, target_device_public_id=client_a.device_public_id, published_by="admin"
        )
        dev_suite_pusher()
        client_a.coordinator.pull_and_apply(ENTITY_TYPE)
        with client_a.database.session_scope() as session:
            company = CompanyRepository(session).list_active()[0]
            settings = CompanySettingsRepository(session, company_id=company.id).get_for_company()
            settings.remote_config_restart_required = False

        # An unrelated change (print margin) must not set the flag again.
        print_profile = configuration_service.list_print_profiles()[0]
        configuration_service.update_print_profile(
            print_profile.id, name=print_profile.name, paper_size=print_profile.paper_size, margin_mm=25
        )
        publish_service.publish(
            bundle.id, customer_id=customer.id, target_device_public_id=client_a.device_public_id, published_by="admin"
        )
        dev_suite_pusher()
        client_a.coordinator.pull_and_apply(ENTITY_TYPE)
        with client_a.database.session_scope() as session:
            company = CompanyRepository(session).list_active()[0]
            settings = CompanySettingsRepository(session, company_id=company.id).get_for_company()
            assert settings.remote_config_restart_required is False
            assert settings.print_settings["margin_mm"] == 25

        # A language change must set it.
        theme = configuration_service.list_theme_profiles()[0]
        configuration_service.update_theme_profile(
            theme.id, name=theme.name, mode=theme.mode, primary_color=theme.primary_color,
            secondary_color=theme.secondary_color, language="en",
        )
        publish_service.publish(
            bundle.id, customer_id=customer.id, target_device_public_id=client_a.device_public_id, published_by="admin"
        )
        dev_suite_pusher()
        client_a.coordinator.pull_and_apply(ENTITY_TYPE)

        with client_a.database.session_scope() as session:
            company = CompanyRepository(session).list_active()[0]
            settings = CompanySettingsRepository(session, company_id=company.id).get_for_company()
            assert settings.remote_config_restart_required is True
            assert settings.language == "en"

    def test_rollback_is_delivered_and_applied(
        self, publish_service, configuration_service, customer_service, dev_suite_pusher, client_a
    ) -> None:
        bundle = _build_bundle(configuration_service)
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")

        v1 = publish_service.publish(
            bundle.id, customer_id=customer.id, target_device_public_id=client_a.device_public_id, published_by="admin"
        )
        dev_suite_pusher()
        client_a.coordinator.pull_and_apply(ENTITY_TYPE)

        theme = configuration_service.list_theme_profiles()[0]
        configuration_service.update_theme_profile(
            theme.id, name=theme.name, mode=theme.mode, primary_color="#FF0000", secondary_color=theme.secondary_color
        )
        publish_service.publish(
            bundle.id, customer_id=customer.id, target_device_public_id=client_a.device_public_id, published_by="admin"
        )
        dev_suite_pusher()
        client_a.coordinator.pull_and_apply(ENTITY_TYPE)
        with client_a.database.session_scope() as session:
            company = CompanyRepository(session).list_active()[0]
            settings = CompanySettingsRepository(session, company_id=company.id).get_for_company()
            assert settings.theme_primary_color == "#FF0000"

        publish_service.rollback(client_a.device_public_id, to_publication_id=v1.id, published_by="admin")
        dev_suite_pusher()
        client_a.coordinator.pull_and_apply(ENTITY_TYPE)

        with client_a.database.session_scope() as session:
            company = CompanyRepository(session).list_active()[0]
            settings = CompanySettingsRepository(session, company_id=company.id).get_for_company()
            assert settings.theme_primary_color == "#1976D2"
            assert settings.remote_config_version == 3


# ---------------------------------------------------------------------------
# Isolation.
# ---------------------------------------------------------------------------


class TestZeroImpactOnOtherApplications:
    def test_client_sync_tables_live_only_in_the_attendance_client_schema(self) -> None:
        from developer_suite.database.base import Base as DeveloperSuiteBase
        from models.base import Base as AttendanceBase
        from server.database.base import Base as ServerBase

        for table_name in ("client_sync_credential", "client_sync_cursors"):
            assert table_name in AttendanceBase.metadata.tables
            assert table_name not in DeveloperSuiteBase.metadata.tables
            assert table_name not in ServerBase.metadata.tables

    def test_sync_protocol_imports_nothing_from_developer_suite_or_server(self) -> None:
        import ast
        import inspect

        import sync.client as client_module
        import sync.configuration_apply as apply_module
        import sync.coordinator as coordinator_module
        import sync.protocol as protocol_module

        for module in (protocol_module, client_module, coordinator_module, apply_module):
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("developer_suite"), module.__name__
                    assert not node.module.startswith("server"), module.__name__
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("developer_suite"), module.__name__
                        assert not alias.name.startswith("server"), module.__name__

    def test_importing_sync_package_does_not_create_developer_suite_config_singleton(self) -> None:
        import sync.coordinator  # noqa: F401

        assert developer_suite_config_module._config_instance is None

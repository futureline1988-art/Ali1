"""Tests for Phase 8: Customer as the first real business entity synchronized
through the Attendance Server's generic protocol built in Phases 6-7.1.

Exercises the full, real stack:

* The Attendance Server's actual FastAPI application
  (``server.api.app.create_app``), served by a real ``uvicorn`` server
  bound to an OS-assigned loopback port in a background thread (see
  ``running_server`` below) — a genuine HTTP request/response cycle
  over a real socket through real routing, auth, and
  (de)serialization, exercising
  :class:`~developer_suite.sync.client.SyncClient` exactly as it runs
  in production. (``httpx.ASGITransport`` was tried first and rejected:
  it only implements the *async* transport interface, while this
  project's ``SyncClient`` is deliberately synchronous to match every
  other Developer Suite service's session-per-call style — see
  :mod:`developer_suite.sync.client`'s docstring.)
* Two independent, separately enrolled Developer Suite "installations"
  (``device_a``/``device_b`` below), each its own SQLite database, both
  pointed at the same running Attendance Server — simulating two
  customers' vendor installations (or, just as validly, two Developer
  Suite workstations) synchronizing through one shared backend.

Every server-side behavior exercised here (push, pull, conflict
detection, conflict resolution) is exactly the Phase 7/7.1 mechanism,
completely unmodified — see ``TestZeroImpactOnOtherApplications`` at
the bottom of this file for an explicit check that no Customer
-specific code leaked into it.
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

from config import DatabaseConfig
from database.database import Database

import server.config as server_config_module
from server.api.app import create_app
from server.auth.tokens import issue_token
from server.config import ServerConfig, get_server_config
from server.database.bootstrap import build_database as build_server_database
from server.models.sync import ChangeStatus as ServerChangeStatus
from server.services.sync_service import SyncService

import developer_suite.config as developer_suite_config_module
from developer_suite.config import DeveloperSuiteConfig, DeveloperSuitePaths, get_developer_suite_config
from developer_suite.database.bootstrap import build_database as build_dev_suite_database
from developer_suite.models.sync_state import OutboxStatus
from developer_suite.models.sync_state import SyncOperation as DevSyncOperation
from developer_suite.repositories.customer_repository import CustomerRepository
from developer_suite.repositories.sync_repository import (
    SyncCredentialRepository,
    SyncEntityVersionRepository,
    SyncOutboxRepository,
)
from developer_suite.services.customer_service import CustomerService
from developer_suite.sync.client import DeviceType as DevSuiteDeviceType
from developer_suite.sync.coordinator import DeviceNotEnrolledError, SyncCoordinator
from developer_suite.sync.customer_sync import ENTITY_TYPE as CUSTOMER_ENTITY_TYPE
from developer_suite.sync.customer_sync import register_customer_sync


@pytest.fixture(autouse=True)
def _reset_developer_suite_config_singleton():
    """Keep ``developer_suite.config``'s process-wide singleton untouched by this file.

    Most tests below build :class:`~developer_suite.config.DeveloperSuiteConfig`
    instances directly (see ``_standalone_dev_suite_config``), bypassing
    :func:`~developer_suite.config.get_developer_suite_config` — but
    :mod:`developer_suite.security.field_encryption` still resolves its
    key file through that same singleton (see its own docstring: the
    right choice for the one real Developer Suite process a real
    installation ever runs, which is all it needs to support). Encrypting
    this file's ``SyncDeviceCredential.api_key`` values therefore
    populates the singleton as a side effect purely of this test file
    existing, which would otherwise leak into and fail
    ``tests/test_server_phase6.py``'s
    ``test_importing_server_does_not_create_developer_suite_config_singleton``
    whenever it happens to run afterward in the same process.
    """
    developer_suite_config_module._config_instance = None
    yield
    developer_suite_config_module._config_instance = None


# ---------------------------------------------------------------------------
# Attendance Server fixtures (mirrors tests/test_server_phase7.py's own
# fixture shapes exactly, so this file needs no new server-side setup).
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
    """Serve ``server_app`` for real over a loopback socket, for the life of one test.

    Bound to an OS-assigned free port (``("127.0.0.1", 0)``) rather
    than a fixed one, so tests can run in parallel without a port
    collision.
    """
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
def customer_service(dev_suite_database: Database) -> CustomerService:
    return CustomerService(dev_suite_database)


@dataclass
class Installation:
    """One enrolled, sync-ready Developer Suite "installation" used by a test."""

    config: DeveloperSuiteConfig
    database: Database
    coordinator: SyncCoordinator
    customer_service: CustomerService


def _standalone_dev_suite_config(tmp_path, name: str, attendance_server_url: str) -> DeveloperSuiteConfig:
    """Build a config for a second, independent Developer Suite database.

    Bypasses the process-wide :func:`~developer_suite.config.get_developer_suite_config`
    singleton entirely (constructing the dataclass directly) since two
    simulated installations need two genuinely separate databases at
    once, which one env-var-driven singleton cannot represent.
    """
    paths = DeveloperSuitePaths.default()
    paths.ensure_created()
    return DeveloperSuiteConfig(
        database=DatabaseConfig(sqlite_path=tmp_path / f"{name}.db", database_name="developer_suite"),
        paths=paths,
        attendance_server_url=attendance_server_url,
    )


def _make_installation(tmp_path, name: str, server_url: str, admin_bearer_token: str) -> Installation:
    config = _standalone_dev_suite_config(tmp_path, name, server_url)
    database = build_dev_suite_database(config)
    coordinator = SyncCoordinator(database, config)
    register_customer_sync(coordinator)
    coordinator.enroll(
        admin_bearer_token=admin_bearer_token, name=name, device_type=DevSuiteDeviceType.DEVELOPER_SUITE
    )
    return Installation(
        config=config, database=database, coordinator=coordinator, customer_service=CustomerService(database)
    )


@pytest.fixture
def device_a(tmp_path, running_server_url: str, admin_bearer_token: str) -> Installation:
    installation = _make_installation(tmp_path, "device_a", running_server_url, admin_bearer_token)
    yield installation
    installation.database.dispose()


@pytest.fixture
def device_b(tmp_path, running_server_url: str, admin_bearer_token: str) -> Installation:
    installation = _make_installation(tmp_path, "device_b", running_server_url, admin_bearer_token)
    yield installation
    installation.database.dispose()


# ---------------------------------------------------------------------------
# The local outbox: coalescing, no server involved.
# ---------------------------------------------------------------------------


class TestOutboxCoalescing:
    def test_create_queues_a_pending_create_entry(self, dev_suite_database, customer_service) -> None:
        customer = customer_service.create_customer(
            company_name="Acme Co", contact_name="Jane Doe", email="jane@acme.example"
        )
        with dev_suite_database.session_scope() as session:
            entry = SyncOutboxRepository(session).get_by_entity(CUSTOMER_ENTITY_TYPE, str(customer.public_id))
            assert entry is not None
            assert entry.operation is DevSyncOperation.CREATE
            assert entry.status is OutboxStatus.PENDING
            assert entry.base_version == 0
            assert entry.payload["company_name"] == "Acme Co"
            assert entry.payload["email"] == "jane@acme.example"
            # id/created_at/updated_at are local bookkeeping, never part
            # of the cross-system payload.
            assert "id" not in entry.payload
            assert "created_at" not in entry.payload
            assert "updated_at" not in entry.payload

    def test_update_after_pending_create_coalesces_into_one_create_entry(
        self, dev_suite_database, customer_service
    ) -> None:
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        customer_service.update_customer(customer.id, company_name="Acme Corp", contact_name="Jane Doe")

        with dev_suite_database.session_scope() as session:
            entries = SyncOutboxRepository(session).list_pending()
            assert len(entries) == 1
            assert entries[0].operation is DevSyncOperation.CREATE
            assert entries[0].payload["company_name"] == "Acme Corp"
            assert entries[0].base_version == 0

    def test_create_then_delete_before_any_push_cancels_out(self, dev_suite_database, customer_service) -> None:
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        customer_service.delete_customer(customer.id)

        with dev_suite_database.session_scope() as session:
            entry = SyncOutboxRepository(session).get_by_entity(CUSTOMER_ENTITY_TYPE, str(customer.public_id))
            assert entry is None

    def test_further_edits_after_push_keep_the_freshly_confirmed_base_version(
        self, dev_suite_database, customer_service
    ) -> None:
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        entity_id = str(customer.public_id)

        # Simulate a successful push that already happened: the outbox
        # entry is cleared and the entity's confirmed server version
        # advances to 3.
        with dev_suite_database.session_scope() as session:
            outbox = SyncOutboxRepository(session)
            outbox.mark_pushed(outbox.get_by_entity(CUSTOMER_ENTITY_TYPE, entity_id))
            SyncEntityVersionRepository(session).set_known_version(CUSTOMER_ENTITY_TYPE, entity_id, 3)

        customer_service.update_customer(customer.id, company_name="Acme Corp", contact_name="Jane Doe")
        customer_service.update_customer(customer.id, company_name="Acme Corp 2", contact_name="Jane Doe")

        with dev_suite_database.session_scope() as session:
            entries = SyncOutboxRepository(session).list_pending()
            assert len(entries) == 1
            assert entries[0].operation is DevSyncOperation.UPDATE
            assert entries[0].base_version == 3
            assert entries[0].payload["company_name"] == "Acme Corp 2"

    def test_suspend_and_reactivate_queue_update_entries(self, dev_suite_database, customer_service) -> None:
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        with dev_suite_database.session_scope() as session:
            SyncOutboxRepository(session).mark_pushed(
                SyncOutboxRepository(session).get_by_entity(CUSTOMER_ENTITY_TYPE, str(customer.public_id))
            )

        customer_service.suspend(customer.id)

        with dev_suite_database.session_scope() as session:
            entry = SyncOutboxRepository(session).get_by_entity(CUSTOMER_ENTITY_TYPE, str(customer.public_id))
            assert entry is not None
            assert entry.operation is DevSyncOperation.UPDATE
            assert entry.payload["status"] == "suspended"


# ---------------------------------------------------------------------------
# Enrollment.
# ---------------------------------------------------------------------------


class TestEnrollment:
    def test_enroll_persists_an_encrypted_credential(
        self, tmp_path, running_server_url, admin_bearer_token
    ) -> None:
        config = _standalone_dev_suite_config(tmp_path, "enroll_test", running_server_url)
        database = build_dev_suite_database(config)
        coordinator = SyncCoordinator(database, config)

        assert coordinator.is_enrolled() is False
        coordinator.enroll(
            admin_bearer_token=admin_bearer_token, name="Test Install", device_type=DevSuiteDeviceType.DEVELOPER_SUITE
        )
        assert coordinator.is_enrolled() is True

        with database.session_scope() as session:
            credential = SyncCredentialRepository(session).get()
            assert credential is not None
            assert uuid.UUID(credential.device_public_id)
            assert credential.api_key
            assert credential.server_url == config.attendance_server_url

        # The plaintext key never touches the raw sqlite file.
        raw_bytes = config.database.sqlite_path.read_bytes()
        assert credential.api_key.encode("utf-8") not in raw_bytes
        database.dispose()

    def test_push_pending_without_enrollment_raises_once_something_is_queued(
        self, tmp_path, running_server_url
    ) -> None:
        config = _standalone_dev_suite_config(tmp_path, "unenrolled", running_server_url)
        database = build_dev_suite_database(config)
        CustomerService(database).create_customer(company_name="Acme Co", contact_name="Jane Doe")

        coordinator = SyncCoordinator(database, config)
        with pytest.raises(DeviceNotEnrolledError):
            coordinator.push_pending()
        database.dispose()

    def test_push_pending_with_nothing_queued_does_not_require_enrollment(
        self, tmp_path, running_server_url
    ) -> None:
        config = _standalone_dev_suite_config(tmp_path, "unenrolled_empty", running_server_url)
        database = build_dev_suite_database(config)
        coordinator = SyncCoordinator(database, config)

        summary = coordinator.push_pending()
        assert summary.applied == 0
        database.dispose()


# ---------------------------------------------------------------------------
# The complete lifecycle: create/update/delete -> push -> pull, between two
# independent installations sharing one Attendance Server.
# ---------------------------------------------------------------------------


class TestFullLifecyclePushPull:
    def test_create_pushes_and_is_pulled_by_another_installation(self, device_a, device_b) -> None:
        customer = device_a.customer_service.create_customer(
            company_name="Acme Co", contact_name="Jane Doe", email="jane@acme.example"
        )

        push_summary = device_a.coordinator.push_pending()
        assert push_summary.applied == 1
        assert push_summary.conflict == 0
        assert push_summary.rejected == 0

        pull_summary = device_b.coordinator.pull_and_apply(CUSTOMER_ENTITY_TYPE)
        assert pull_summary.applied == 1

        with device_b.database.session_scope() as session:
            mirrored = CustomerRepository(session).get_by_public_id(customer.public_id)
            assert mirrored is not None
            assert mirrored.company_name == "Acme Co"
            assert mirrored.email == "jane@acme.example"

    def test_update_pushes_and_is_applied_by_another_installation(self, device_a, device_b) -> None:
        customer = device_a.customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        device_a.coordinator.push_pending()
        device_b.coordinator.pull_and_apply(CUSTOMER_ENTITY_TYPE)

        device_a.customer_service.update_customer(customer.id, company_name="Acme Corp", contact_name="Jane Doe")
        push_summary = device_a.coordinator.push_pending()
        assert push_summary.applied == 1

        pull_summary = device_b.coordinator.pull_and_apply(CUSTOMER_ENTITY_TYPE)
        assert pull_summary.applied == 1
        with device_b.database.session_scope() as session:
            mirrored = CustomerRepository(session).get_by_public_id(customer.public_id)
            assert mirrored.company_name == "Acme Corp"

    def test_delete_pushes_and_is_soft_deleted_by_another_installation(self, device_a, device_b) -> None:
        customer = device_a.customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        device_a.coordinator.push_pending()
        device_b.coordinator.pull_and_apply(CUSTOMER_ENTITY_TYPE)

        device_a.customer_service.delete_customer(customer.id)
        push_summary = device_a.coordinator.push_pending()
        assert push_summary.applied == 1

        pull_summary = device_b.coordinator.pull_and_apply(CUSTOMER_ENTITY_TYPE)
        assert pull_summary.applied == 1
        with device_b.database.session_scope() as session:
            visible = CustomerRepository(session).get_by_public_id(customer.public_id)
            assert visible is None
            deleted = CustomerRepository(session).get_by_public_id(customer.public_id, include_deleted=True)
            assert deleted is not None
            assert deleted.is_deleted is True

    def test_repeat_pull_with_nothing_new_applies_nothing(self, device_a, device_b) -> None:
        device_a.customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        device_a.coordinator.push_pending()

        first = device_b.coordinator.pull_and_apply(CUSTOMER_ENTITY_TYPE)
        assert first.applied == 1

        second = device_b.coordinator.pull_and_apply(CUSTOMER_ENTITY_TYPE)
        assert second.applied == 0
        assert second.next_cursor == first.next_cursor


# ---------------------------------------------------------------------------
# Conflict detection and resolution: the reused, unmodified Phase 7/7.1
# mechanism, exercised for real by two installations disagreeing about the
# same customer.
# ---------------------------------------------------------------------------


class TestConflictDetectionAndResolution:
    def test_two_installations_editing_the_same_customer_conflicts(
        self, device_a, device_b, server_database
    ) -> None:
        customer = device_a.customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        device_a.coordinator.push_pending()
        device_b.coordinator.pull_and_apply(CUSTOMER_ENTITY_TYPE)

        # Device A edits and pushes first.
        device_a.customer_service.update_customer(customer.id, company_name="Acme Corp (A)", contact_name="Jane Doe")
        a_push = device_a.coordinator.push_pending()
        assert a_push.applied == 1

        # Device B, still believing the pre-A version, edits concurrently.
        with device_b.database.session_scope() as session:
            mirrored_id = CustomerRepository(session).get_by_public_id(customer.public_id).id
        device_b.customer_service.update_customer(mirrored_id, company_name="Acme Corp (B)", contact_name="Jane Doe")
        b_push = device_b.coordinator.push_pending()

        assert b_push.applied == 0
        assert b_push.conflict == 1

        with device_b.database.session_scope() as session:
            entry = SyncOutboxRepository(session).get_by_entity(CUSTOMER_ENTITY_TYPE, str(customer.public_id))
            assert entry is not None
            assert entry.status is OutboxStatus.CONFLICT
            assert entry.conflict_reason is not None

        conflicts = SyncService(server_database).list_conflicts()
        assert len(conflicts) == 1
        assert conflicts[0].entity_id == str(customer.public_id)
        assert conflicts[0].payload["company_name"] == "Acme Corp (B)"

    def test_admin_force_applies_conflict_and_the_losing_device_pulls_the_resolution(
        self, device_a, device_b, server_database
    ) -> None:
        customer = device_a.customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        device_a.coordinator.push_pending()
        device_a.coordinator.pull_and_apply(CUSTOMER_ENTITY_TYPE)  # catch up to its own create
        device_b.coordinator.pull_and_apply(CUSTOMER_ENTITY_TYPE)

        device_a.customer_service.update_customer(customer.id, company_name="Acme Corp (A)", contact_name="Jane Doe")
        device_a.coordinator.push_pending()
        device_a.coordinator.pull_and_apply(CUSTOMER_ENTITY_TYPE)  # catch up to its own update

        with device_b.database.session_scope() as session:
            mirrored_id = CustomerRepository(session).get_by_public_id(customer.public_id).id
        device_b.customer_service.update_customer(mirrored_id, company_name="Acme Corp (B)", contact_name="Jane Doe")
        b_push = device_b.coordinator.push_pending()
        assert b_push.conflict == 1

        sync_service = SyncService(server_database)
        conflicts = sync_service.list_conflicts()
        assert len(conflicts) == 1
        resolved = sync_service.resolve_conflict(conflicts[0].id, apply_incoming=True)
        assert resolved.status is ServerChangeStatus.APPLIED
        assert resolved.payload["company_name"] == "Acme Corp (B)"

        # Device A never had this edit locally at all; pulling the
        # resolution is how it ever learns "B" won.
        pull_summary = device_a.coordinator.pull_and_apply(CUSTOMER_ENTITY_TYPE)
        assert pull_summary.applied == 1
        with device_a.database.session_scope() as session:
            mirrored = CustomerRepository(session).get_by_public_id(customer.public_id)
            assert mirrored.company_name == "Acme Corp (B)"

    def test_admin_discards_conflict_and_the_authoritative_history_still_wins_on_pull(
        self, device_a, device_b, server_database
    ) -> None:
        customer = device_a.customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        device_a.coordinator.push_pending()
        device_b.coordinator.pull_and_apply(CUSTOMER_ENTITY_TYPE)

        device_a.customer_service.update_customer(customer.id, company_name="Acme Corp (A)", contact_name="Jane Doe")
        device_a.coordinator.push_pending()  # device_b has not pulled this yet

        with device_b.database.session_scope() as session:
            mirrored_id = CustomerRepository(session).get_by_public_id(customer.public_id).id
        device_b.customer_service.update_customer(mirrored_id, company_name="Acme Corp (B)", contact_name="Jane Doe")
        b_push = device_b.coordinator.push_pending()
        assert b_push.conflict == 1

        sync_service = SyncService(server_database)
        conflicts = sync_service.list_conflicts()
        resolved = sync_service.resolve_conflict(conflicts[0].id, apply_incoming=False)
        assert resolved.status is ServerChangeStatus.REJECTED

        # Device B still hadn't pulled A's earlier applied update -
        # that, not anything from B's own discarded attempt, is what a
        # pull now delivers.
        pull_summary = device_b.coordinator.pull_and_apply(CUSTOMER_ENTITY_TYPE)
        assert pull_summary.applied == 1
        with device_b.database.session_scope() as session:
            mirrored = CustomerRepository(session).get_by_public_id(customer.public_id)
            assert mirrored.company_name == "Acme Corp (A)"

        # Discarding never appends a new change, so a further pull is empty.
        assert device_b.coordinator.pull_and_apply(CUSTOMER_ENTITY_TYPE).applied == 0

        # The local outbox entry stays marked CONFLICT - clearing it is
        # not automatic; only a fresh local edit supersedes it (see
        # SyncOutboxRepository.enqueue's docstring). Documented as
        # residual behavior, not a defect, in Phase 8's summary.
        with device_b.database.session_scope() as session:
            entry = SyncOutboxRepository(session).get_by_entity(CUSTOMER_ENTITY_TYPE, str(customer.public_id))
            assert entry is not None
            assert entry.status is OutboxStatus.CONFLICT


# ---------------------------------------------------------------------------
# Isolation: the generic protocol still knows nothing about Customer, and
# the new local tables live only in the Developer Suite's own schema.
# ---------------------------------------------------------------------------


class TestZeroImpactOnOtherApplications:
    def test_sync_state_tables_live_only_in_the_developer_suite_schema(self) -> None:
        from developer_suite.database.base import Base as DeveloperSuiteBase
        from models.base import Base as AttendanceBase
        from server.database.base import Base as ServerBase

        for table_name in (
            "sync_device_credential",
            "sync_cursors",
            "sync_entity_versions",
            "sync_outbox_entries",
        ):
            assert table_name in DeveloperSuiteBase.metadata.tables
            assert table_name not in AttendanceBase.metadata.tables
            assert table_name not in ServerBase.metadata.tables

    def test_server_sync_protocol_still_imports_nothing_from_developer_suite(self) -> None:
        """The server's generic sync engine must stay reachable only over HTTP.

        A prose mention of "customers" as an example future domain
        (already present in these modules' own docstrings since Phase
        7) is fine and expected; an actual Python import of anything
        Developer-Suite-specific would mean the two applications are
        no longer talking only over the HTTP boundary Phase 6
        established, which is the thing this test actually guards.
        """
        import ast
        import inspect

        import server.models.sync as server_sync_models
        import server.services.sync_service as server_sync_service

        for module in (server_sync_service, server_sync_models):
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("developer_suite")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("developer_suite")

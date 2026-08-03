"""Tests for Phase 9: automatic, periodic synchronization in the Developer Suite.

Two layers of tests:

* Unit-level tests against a ``FakeCoordinator`` test double (below) —
  fast and deterministic, covering scheduler behavior (retry, offline
  detection, concurrency, non-blocking startup, status reporting) that
  would be slow or awkward to reproduce by actually breaking a real
  network connection.
* One end-to-end integration test reusing the *real*
  :class:`~developer_suite.sync.coordinator.SyncCoordinator` and a real
  running Attendance Server (mirroring
  :mod:`tests.test_phase8_customer_sync`'s own fixtures), proving the
  scheduler genuinely drives real push/pull rather than only satisfying
  a mocked interface.

Every unit test below constructs :class:`~developer_suite.sync.scheduler.SyncSchedulerService`
with a short ``retry_backoff_seconds`` so retry tests stay fast without
weakening what they verify (the retry *count*/*classification* logic,
not real-world timing).
"""

from __future__ import annotations

import os
import socket
import threading
import time
from dataclasses import dataclass, field

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

import developer_suite.config as developer_suite_config_module
from developer_suite.config import DeveloperSuiteConfig, DeveloperSuitePaths, get_developer_suite_config
from developer_suite.database.bootstrap import build_database as build_dev_suite_database
from developer_suite.repositories.customer_repository import CustomerRepository
from developer_suite.services.customer_service import CustomerService
from developer_suite.sync.client import DeviceType as DevSuiteDeviceType
from developer_suite.sync.client import SyncAuthError, SyncConnectionError
from developer_suite.sync.coordinator import SyncCoordinator
from developer_suite.sync.customer_sync import register_customer_sync
from developer_suite.sync.scheduler import SyncSchedulerService
from developer_suite.sync.status import SyncState


@pytest.fixture(autouse=True)
def _reset_developer_suite_config_singleton():
    """See tests/test_phase8_customer_sync.py's identical fixture for why this is needed."""
    developer_suite_config_module._config_instance = None
    yield
    developer_suite_config_module._config_instance = None


# ---------------------------------------------------------------------------
# A minimal, generic test double for SyncCoordinator's public surface. Never
# mentions Customer -- the scheduler doesn't either, and this proves it.
# ---------------------------------------------------------------------------


@dataclass
class FakeCoordinator:
    enrolled: bool = True
    entity_types: tuple[str, ...] = ("customer",)
    pending_count: int = 0
    push_sleep_seconds: float = 0.0

    push_calls: list[threading.Thread] = field(default_factory=list)
    pull_calls: list[tuple[str, threading.Thread]] = field(default_factory=list)
    _push_effects: list[Exception] = field(default_factory=list)

    def is_enrolled(self) -> bool:
        return self.enrolled

    def registered_entity_types(self) -> list[str]:
        return list(self.entity_types)

    def count_pending(self) -> int:
        return self.pending_count

    def queue_push_failure(self, exc: Exception, *, times: int = 1) -> None:
        self._push_effects.extend([exc] * times)

    def push_pending(self) -> None:
        self.push_calls.append(threading.current_thread())
        if self.push_sleep_seconds:
            time.sleep(self.push_sleep_seconds)
        if self._push_effects:
            raise self._push_effects.pop(0)

    def pull_and_apply(self, entity_type: str) -> None:
        self.pull_calls.append((entity_type, threading.current_thread()))


def _scheduler_config(*, sync_enabled: bool = True, sync_interval_seconds: int = 60) -> DeveloperSuiteConfig:
    return DeveloperSuiteConfig(sync_enabled=sync_enabled, sync_interval_seconds=sync_interval_seconds)


def _fast_scheduler(coordinator: FakeCoordinator, **config_kwargs) -> SyncSchedulerService:
    """A scheduler with near-zero retry backoff, so retry tests run in milliseconds."""
    return SyncSchedulerService(
        coordinator,
        _scheduler_config(**config_kwargs),
        max_retries_per_cycle=3,
        retry_backoff_seconds=0.01,
    )


# ---------------------------------------------------------------------------
# Startup / shutdown.
# ---------------------------------------------------------------------------


class TestSchedulerStartup:
    def test_start_registers_and_starts_the_job(self) -> None:
        scheduler = _fast_scheduler(FakeCoordinator(), sync_interval_seconds=30)
        scheduler.start()
        try:
            assert scheduler._scheduler.running is True
            assert scheduler._scheduler.get_job("developer_suite_background_sync") is not None
        finally:
            scheduler.shutdown()

    def test_start_returns_promptly_without_blocking(self) -> None:
        scheduler = _fast_scheduler(FakeCoordinator(), sync_interval_seconds=60)
        started_at = time.monotonic()
        scheduler.start()
        try:
            assert time.monotonic() - started_at < 1.0
        finally:
            scheduler.shutdown()

    def test_disabled_by_configuration_never_starts(self) -> None:
        scheduler = _fast_scheduler(FakeCoordinator(), sync_enabled=False)
        scheduler.start()
        assert scheduler._scheduler.running is False


class TestSchedulerShutdown:
    def test_shutdown_stops_a_running_scheduler(self) -> None:
        scheduler = _fast_scheduler(FakeCoordinator())
        scheduler.start()
        assert scheduler._scheduler.running is True
        scheduler.shutdown()
        assert scheduler._scheduler.running is False

    def test_shutdown_before_start_is_a_safe_no_op(self) -> None:
        scheduler = _fast_scheduler(FakeCoordinator())
        scheduler.shutdown()  # must not raise
        assert scheduler._scheduler.running is False

    def test_shutdown_is_idempotent(self) -> None:
        scheduler = _fast_scheduler(FakeCoordinator())
        scheduler.start()
        scheduler.shutdown()
        scheduler.shutdown()  # must not raise a second time


# ---------------------------------------------------------------------------
# One cycle's behavior.
# ---------------------------------------------------------------------------


class TestSuccessfulSynchronization:
    def test_pushes_then_pulls_every_registered_entity_type(self) -> None:
        coordinator = FakeCoordinator(entity_types=("customer", "widget"))
        scheduler = _fast_scheduler(coordinator)

        status = scheduler.sync_now()

        assert len(coordinator.push_calls) == 1
        assert [entity_type for entity_type, _ in coordinator.pull_calls] == ["customer", "widget"]
        assert status.state is SyncState.IDLE
        assert status.last_success_at is not None
        assert status.last_error_message is None
        assert status.consecutive_failures == 0

    def test_not_enrolled_is_idle_not_an_error(self) -> None:
        coordinator = FakeCoordinator(enrolled=False)
        scheduler = _fast_scheduler(coordinator)

        status = scheduler.sync_now()

        assert coordinator.push_calls == []
        assert coordinator.pull_calls == []
        assert status.state is SyncState.IDLE
        assert status.last_error_message is None


class TestOfflineRecovery:
    def test_persistent_connection_failure_reports_offline(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.queue_push_failure(SyncConnectionError("no route to host"), times=10)
        scheduler = _fast_scheduler(coordinator)

        status = scheduler.sync_now()

        assert status.state is SyncState.OFFLINE
        assert status.last_success_at is None
        assert status.last_failure_at is not None
        assert status.consecutive_failures == 1
        assert coordinator.pull_calls == []  # never got past the push step

    def test_recovers_automatically_once_connectivity_returns(self) -> None:
        coordinator = FakeCoordinator()
        # Exactly max_retries_per_cycle failures: the first sync_now()
        # cycle exhausts all of them (ending OFFLINE); nothing is left
        # queued, so the second cycle finds push_pending succeeding.
        coordinator.queue_push_failure(SyncConnectionError("no route to host"), times=3)
        scheduler = _fast_scheduler(coordinator)

        offline_status = scheduler.sync_now()
        assert offline_status.state is SyncState.OFFLINE

        # Connectivity "returns": no more queued failures, so the very
        # next cycle (a later scheduled tick, in production) succeeds.
        recovered_status = scheduler.sync_now()

        assert recovered_status.state is SyncState.IDLE
        assert recovered_status.last_success_at is not None
        assert recovered_status.consecutive_failures == 0
        # The offline episode's timestamp is kept as history, not erased.
        assert recovered_status.last_failure_at == offline_status.last_failure_at


class TestRetryBehavior:
    def test_transient_failures_within_the_retry_budget_still_succeed(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.queue_push_failure(SyncConnectionError("timeout"), times=2)
        scheduler = _fast_scheduler(coordinator)

        status = scheduler.sync_now()

        assert len(coordinator.push_calls) == 3  # 2 failures + 1 success
        assert status.state is SyncState.IDLE

    def test_failures_beyond_the_retry_budget_end_the_cycle_as_offline(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.queue_push_failure(SyncConnectionError("timeout"), times=3)
        scheduler = _fast_scheduler(coordinator)

        status = scheduler.sync_now()

        assert len(coordinator.push_calls) == 3  # exactly max_retries_per_cycle attempts
        assert status.state is SyncState.OFFLINE

    def test_non_connection_failures_are_never_retried(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.queue_push_failure(SyncAuthError("credential rejected"))
        scheduler = _fast_scheduler(coordinator)

        status = scheduler.sync_now()

        assert len(coordinator.push_calls) == 1  # no retry attempted
        assert status.state is SyncState.ERROR
        assert "credential rejected" in status.last_error_message


# ---------------------------------------------------------------------------
# Concurrency and non-blocking behavior.
# ---------------------------------------------------------------------------


class TestConcurrentSafety:
    def test_overlapping_manual_calls_run_only_one_cycle(self) -> None:
        coordinator = FakeCoordinator(push_sleep_seconds=0.3)
        scheduler = _fast_scheduler(coordinator)

        results: list[object] = []

        def _call() -> None:
            results.append(scheduler.sync_now())

        threads = [threading.Thread(target=_call) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        assert len(coordinator.push_calls) == 1
        assert len(results) == 3  # every caller got a status back, none raised

    def test_a_call_that_finds_a_cycle_in_flight_does_not_block(self) -> None:
        coordinator = FakeCoordinator(push_sleep_seconds=1.0)
        scheduler = _fast_scheduler(coordinator)

        first_thread = threading.Thread(target=scheduler.sync_now)
        first_thread.start()
        time.sleep(0.1)  # let the first cycle actually acquire the lock

        started_at = time.monotonic()
        scheduler.sync_now()  # must return immediately, not wait ~1s for the first cycle
        elapsed = time.monotonic() - started_at

        first_thread.join(timeout=5)
        assert elapsed < 0.5


class TestUIResponsiveness:
    def test_scheduled_ticks_run_on_a_background_thread_not_the_caller(self) -> None:
        coordinator = FakeCoordinator()
        scheduler = _fast_scheduler(coordinator, sync_interval_seconds=1)
        scheduler.start()
        try:
            deadline = time.monotonic() + 5.0
            while not coordinator.push_calls and time.monotonic() < deadline:
                time.sleep(0.05)
            assert coordinator.push_calls, "scheduled job never fired"
            job_thread = coordinator.push_calls[0]
            assert job_thread is not threading.main_thread()
            assert job_thread is not threading.current_thread()
        finally:
            scheduler.shutdown()

    def test_the_calling_thread_stays_free_while_a_cycle_runs_in_the_background(self) -> None:
        coordinator = FakeCoordinator()
        scheduler = _fast_scheduler(coordinator, sync_interval_seconds=1)
        scheduler.start()
        try:
            # Immediately after start(), the calling thread can keep
            # doing its own work -- nothing about scheduling a job
            # blocked it.
            counter = 0
            budget_seconds = 0.2
            deadline = time.monotonic() + budget_seconds
            while time.monotonic() < deadline:
                counter += 1
            assert counter > 0
        finally:
            scheduler.shutdown()


# ---------------------------------------------------------------------------
# Status reporting.
# ---------------------------------------------------------------------------


class TestStatusReporting:
    def test_initial_status_before_any_cycle(self) -> None:
        coordinator = FakeCoordinator(pending_count=3)
        scheduler = _fast_scheduler(coordinator)

        status = scheduler.get_status()

        assert status.state is SyncState.IDLE
        assert status.last_success_at is None
        assert status.last_failure_at is None
        assert status.pending_changes_count == 3

    def test_pending_count_is_always_read_live(self) -> None:
        coordinator = FakeCoordinator(pending_count=5)
        scheduler = _fast_scheduler(coordinator)

        assert scheduler.get_status().pending_changes_count == 5
        coordinator.pending_count = 0
        assert scheduler.get_status().pending_changes_count == 0

    def test_synchronizing_state_is_observable_mid_cycle(self) -> None:
        coordinator = FakeCoordinator(push_sleep_seconds=0.3)
        scheduler = _fast_scheduler(coordinator)

        thread = threading.Thread(target=scheduler.sync_now)
        thread.start()
        time.sleep(0.1)
        try:
            assert scheduler.get_status().state is SyncState.SYNCHRONIZING
        finally:
            thread.join(timeout=5)


# ---------------------------------------------------------------------------
# End-to-end: the real SyncCoordinator, a real running Attendance Server,
# driven entirely by the scheduler rather than manual push/pull calls.
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


def _real_installation(tmp_path, name: str, server_url: str, admin_bearer_token: str):
    paths = DeveloperSuitePaths.default()
    paths.ensure_created()
    config = DeveloperSuiteConfig(
        database=DatabaseConfig(sqlite_path=tmp_path / f"{name}.db", database_name="developer_suite"),
        paths=paths,
        attendance_server_url=server_url,
        sync_interval_seconds=3600,  # only ever driven manually via sync_now() in this test
    )
    database = build_dev_suite_database(config)
    coordinator = SyncCoordinator(database, config)
    register_customer_sync(coordinator)
    coordinator.enroll(
        admin_bearer_token=admin_bearer_token, name=name, device_type=DevSuiteDeviceType.DEVELOPER_SUITE
    )
    scheduler = SyncSchedulerService(coordinator, config, retry_backoff_seconds=0.05)
    return database, coordinator, CustomerService(database), scheduler


class TestRealEndToEndAutomaticSync:
    def test_scheduler_drives_real_push_and_pull_through_the_real_coordinator(
        self, tmp_path, running_server_url, admin_bearer_token
    ) -> None:
        db_a, _coord_a, customers_a, scheduler_a = _real_installation(
            tmp_path, "phase9_device_a", running_server_url, admin_bearer_token
        )
        db_b, _coord_b, customers_b, scheduler_b = _real_installation(
            tmp_path, "phase9_device_b", running_server_url, admin_bearer_token
        )
        try:
            customer = customers_a.create_customer(company_name="Acme Co", contact_name="Jane Doe")

            status_a = scheduler_a.sync_now()
            assert status_a.state is SyncState.IDLE
            assert status_a.pending_changes_count == 0  # the create was actually pushed

            status_b = scheduler_b.sync_now()
            assert status_b.state is SyncState.IDLE

            with db_b.session_scope() as session:
                mirrored = CustomerRepository(session).get_by_public_id(customer.public_id)
                assert mirrored is not None
                assert mirrored.company_name == "Acme Co"
        finally:
            db_a.dispose()
            db_b.dispose()


# ---------------------------------------------------------------------------
# Isolation.
# ---------------------------------------------------------------------------


class TestZeroImpactOnOtherApplications:
    def test_scheduler_module_imports_nothing_from_server_or_the_attendance_client(self) -> None:
        import ast
        import inspect

        import developer_suite.sync.scheduler as scheduler_module
        import developer_suite.sync.status as status_module

        forbidden_prefixes = ("server", "models", "controllers", "ui", "api")
        for module in (scheduler_module, status_module):
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith(forbidden_prefixes), node.module
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith(forbidden_prefixes), alias.name

    def test_scheduler_never_names_a_specific_business_entity_in_code(self) -> None:
        """No identifier or non-docstring string literal names a business entity.

        A prose mention in a docstring (e.g. "Customer today, anything
        else later") is fine and expected — this checks actual code,
        via the same docstring-exclusion technique
        :mod:`tests.test_phase8_customer_sync` uses for the equivalent
        check on the server's own sync modules.
        """
        import ast
        import inspect

        import developer_suite.sync.scheduler as scheduler_module

        tree = ast.parse(inspect.getsource(scheduler_module))
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

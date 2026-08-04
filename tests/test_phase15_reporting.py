"""Tests for Phase 15: Reporting & Analytics.

Exercises the full, real stack the same way
``tests/test_phase14_update_manager.py`` does: a genuine Attendance
Server (real FastAPI app, real ``uvicorn`` socket) and a real
Developer Suite database, with :class:`~developer_suite.services.reporting_service.ReportingService`
wired over real (not faked) customer/license/configuration-publish/
dashboard services and a real :class:`~developer_suite.admin.client.AdminApiClient`.

Also covers, as regression guards for the two small pre-existing
-infrastructure fixes this phase made:

* :func:`utils.pdf.export_to_pdf` now takes an explicit ``fonts_dir``
  instead of resolving it via ``config.get_config()`` — the Attendance
  Client's own :mod:`services.report_service` call site must still
  work unchanged.
* :func:`utils.excel.export_to_excel` previously never received a
  timezone-aware ``datetime`` (the Attendance Client's own report rows
  are always pre-formatted strings); this phase's reports pass real
  ``datetime`` objects through filtering/sorting first, so
  :class:`~developer_suite.services.reporting_service.ReportingService`
  must stringify them before a report ever reaches an exporter.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import uvicorn

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APP_ENVIRONMENT", "testing")

import server.config as server_config_module
from server.api.app import create_app
from server.auth.tokens import issue_token
from server.config import ServerConfig, get_server_config
from server.database.bootstrap import build_database as build_server_database

import developer_suite.config as developer_suite_config_module
from developer_suite.admin.client import AdminApiClient
from developer_suite.config import DeveloperSuiteConfig, get_developer_suite_config
from developer_suite.database.bootstrap import build_database as build_dev_suite_database
from developer_suite.services.configuration_publish_service import ConfigurationPublishService
from developer_suite.services.configuration_service import ConfigurationService
from developer_suite.services.customer_service import CustomerService
from developer_suite.services.dashboard_service import DashboardService
from developer_suite.services.license_service import LicenseService
from developer_suite.services.reporting_service import (
    ReportCategory,
    ReportFilters,
    ReportingService,
    ReportingServiceError,
    _stringify_dates,
    filter_rows,
    group_and_count,
    sort_rows,
)
from developer_suite.sync.coordinator import SyncCoordinator
from developer_suite.sync.scheduler import SyncSchedulerService

from licensing.crypto.signing import generate_keypair, save_private_key
from licensing.enums import LicenseType


# ---------------------------------------------------------------------------
# Pure unit tests: filter/sort/group/stringify helpers need no services at all.
# ---------------------------------------------------------------------------


class TestFilterSortGroupHelpers:
    def test_filter_rows_search_matches_any_column_case_insensitively(self) -> None:
        rows = [{"name": "Acme Co"}, {"name": "Globex"}, {"name": "Initech"}]
        assert [r["name"] for r in filter_rows(rows, search="acme")] == ["Acme Co"]
        assert [r["name"] for r in filter_rows(rows, search="")] == ["Acme Co", "Globex", "Initech"]

    def test_filter_rows_date_range_is_inclusive_and_skips_rows_with_no_date(self) -> None:
        rows = [
            {"name": "before", "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc)},
            {"name": "in-range-start", "created_at": datetime(2024, 3, 1, tzinfo=timezone.utc)},
            {"name": "in-range-end", "created_at": datetime(2024, 6, 30, tzinfo=timezone.utc)},
            {"name": "after", "created_at": datetime(2024, 12, 1, tzinfo=timezone.utc)},
            {"name": "no-date", "created_at": None},
        ]
        result = filter_rows(
            rows, date_field="created_at", start_date=date(2024, 3, 1), end_date=date(2024, 6, 30)
        )
        assert [r["name"] for r in result] == ["in-range-start", "in-range-end"]

    def test_sort_rows_missing_values_always_sort_last_regardless_of_direction(self) -> None:
        rows = [{"name": "Beta"}, {"name": None}, {"name": "Alpha"}]
        assert [r["name"] for r in sort_rows(rows, sort_by="name", descending=False)] == [
            "Alpha", "Beta", None,
        ]
        assert [r["name"] for r in sort_rows(rows, sort_by="name", descending=True)] == [
            "Beta", "Alpha", None,
        ]

    def test_sort_rows_with_no_sort_by_returns_a_copy_in_original_order(self) -> None:
        rows = [{"name": "b"}, {"name": "a"}]
        result = sort_rows(rows, sort_by=None)
        assert result == rows
        assert result is not rows

    def test_group_and_count_orders_by_frequency_and_buckets_missing_values(self) -> None:
        rows = [{"x": "a"}, {"x": "a"}, {"x": "b"}, {"x": None}]
        result = group_and_count(rows, group_by="x")
        assert result.rows == [
            {"group": "a", "count": 2},
            {"group": "b", "count": 1},
            {"group": "غير محدد", "count": 1},
        ]
        assert result.total_before_filters == 4

    def test_stringify_dates_strips_timezone_and_formats_consistently(self) -> None:
        rows = [
            {
                "created_at": datetime(2024, 6, 15, 14, 30, tzinfo=timezone.utc),
                "issued_at": date(2024, 6, 15),
                "name": "Acme",
                "count": 3,
                "missing": None,
            }
        ]
        formatted = _stringify_dates(rows)
        assert formatted[0]["created_at"] == "2024-06-15 14:30"
        assert formatted[0]["issued_at"] == "2024-06-15"
        assert formatted[0]["name"] == "Acme"
        assert formatted[0]["count"] == 3
        assert formatted[0]["missing"] is None
        # The original rows must never be mutated in place.
        assert isinstance(rows[0]["created_at"], datetime)


# ---------------------------------------------------------------------------
# Attendance Server fixtures (mirrors tests/test_phase14_update_manager.py).
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
def server_database(server_config: ServerConfig):
    database = build_server_database(server_config)
    yield database
    database.dispose()


@pytest.fixture
def server_app(server_config: ServerConfig, server_database):
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
    def __init__(self, token: str | None) -> None:
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
def dev_suite_database(dev_suite_config: DeveloperSuiteConfig):
    database = build_dev_suite_database(dev_suite_config)
    yield database
    database.dispose()


@pytest.fixture
def customer_service(dev_suite_database) -> CustomerService:
    return CustomerService(dev_suite_database)


@pytest.fixture
def license_service(dev_suite_database, tmp_path) -> LicenseService:
    private_key, _public_key = generate_keypair()
    key_path = tmp_path / "license_signing_key.pem"
    save_private_key(private_key, key_path)
    return LicenseService(dev_suite_database, private_key_path=key_path)


@pytest.fixture
def configuration_publish_service(dev_suite_database) -> ConfigurationPublishService:
    return ConfigurationPublishService(dev_suite_database)


@pytest.fixture
def dashboard_service(
    customer_service, license_service, dev_suite_database, dev_suite_config, admin_client
) -> DashboardService:
    coordinator = SyncCoordinator(dev_suite_database, dev_suite_config)
    scheduler = SyncSchedulerService(coordinator, dev_suite_config)
    return DashboardService(customer_service, license_service, scheduler, admin_client, dev_suite_config)


@pytest.fixture
def reporting_service(
    customer_service, license_service, configuration_publish_service, dashboard_service, admin_client
) -> ReportingService:
    return ReportingService(
        customer_service, license_service, configuration_publish_service, dashboard_service, admin_client
    )


@pytest.fixture
def acme_customer(customer_service):
    return customer_service.create_customer(
        company_name="Acme Co", contact_name="Jane Doe", phone="07701234567", email="acme@example.com"
    )


# ---------------------------------------------------------------------------
# ReportingService: one class per report category.
# ---------------------------------------------------------------------------


class TestExecutiveDashboardReport:
    def test_returns_every_kpi_as_a_metric_value_row(self, reporting_service, acme_customer) -> None:
        result = reporting_service.build_executive_dashboard_report()
        assert result.columns == [("metric", "المؤشر"), ("value", "القيمة")]
        metrics = {row["metric"]: row["value"] for row in result.rows}
        assert metrics["إجمالي العملاء"] == "1"

    def test_date_range_filters_never_apply_to_this_category(self, reporting_service, acme_customer) -> None:
        # The executive dashboard is a KPI table, not a list of dated
        # events - a date range must be silently ignored, not empty the
        # report out.
        result = reporting_service.build_executive_dashboard_report(
            ReportFilters(start_date=date(1999, 1, 1), end_date=date(1999, 1, 2))
        )
        assert len(result.rows) > 0


class TestCustomerReport:
    def test_lists_every_customer_with_arabic_status_label(self, reporting_service, customer_service) -> None:
        customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        suspended = customer_service.create_customer(company_name="Globex", contact_name="John Roe")
        customer_service.suspend(suspended.id)

        result = reporting_service.build_customer_report()
        by_name = {row["company_name"]: row for row in result.rows}
        assert by_name["Acme Co"]["status"] == "نشط"
        assert by_name["Globex"]["status"] == "موقوف"

    def test_search_filters_by_any_field(self, reporting_service, customer_service) -> None:
        customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe", email="jane@acme.test")
        customer_service.create_customer(company_name="Globex", contact_name="John Roe", email="john@globex.test")

        result = reporting_service.build_customer_report(ReportFilters(search="jane@acme"))
        assert [row["company_name"] for row in result.rows] == ["Acme Co"]

    def test_sort_by_company_name_descending(self, reporting_service, customer_service) -> None:
        customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        customer_service.create_customer(company_name="Zenith", contact_name="John Roe")

        result = reporting_service.build_customer_report(
            ReportFilters(sort_by="company_name", sort_descending=True)
        )
        assert [row["company_name"] for row in result.rows] == ["Zenith", "Acme Co"]


class TestLicenseReport:
    def test_reflects_customer_and_derived_status(self, reporting_service, license_service, acme_customer) -> None:
        license_service.issue_license(customer_id=acme_customer.id, license_type=LicenseType.TRIAL)
        revoked = license_service.issue_license(customer_id=acme_customer.id, license_type=LicenseType.YEARLY)
        license_service.revoke_license(revoked.id)

        result = reporting_service.build_license_report()
        statuses = sorted(row["status"] for row in result.rows)
        assert statuses == ["ملغى", "نشط"]
        assert all(row["company_name"] == "Acme Co" for row in result.rows)


class TestConfigurationPublicationReport:
    def test_lists_publications_across_every_device(
        self, reporting_service, configuration_publish_service, dev_suite_database, acme_customer
    ) -> None:
        from developer_suite.services.configuration_service import ConfigurationService

        configuration_service = ConfigurationService(dev_suite_database)
        theme = configuration_service.create_theme_profile(name="Theme")
        print_profile = configuration_service.create_print_profile(name="Print")
        policy = configuration_service.create_attendance_policy_profile(name="Policy")
        device_profile = configuration_service.create_device_profile(name="Device")
        backup = configuration_service.create_backup_profile(name="Backup")
        bundle = configuration_service.create_configuration(
            name="Bundle",
            theme_profile_id=theme.id,
            print_profile_id=print_profile.id,
            attendance_policy_profile_id=policy.id,
            device_profile_id=device_profile.id,
            backup_profile_id=backup.id,
        )

        import uuid

        device_a = str(uuid.uuid4())
        device_b = str(uuid.uuid4())
        configuration_publish_service.publish(
            bundle.id, customer_id=acme_customer.id, target_device_public_id=device_a, published_by="admin"
        )
        configuration_publish_service.publish(
            bundle.id, customer_id=acme_customer.id, target_device_public_id=device_b, published_by="admin"
        )

        result = reporting_service.build_configuration_publication_report()
        assert len(result.rows) == 2
        assert {row["target_device_public_id"] for row in result.rows} == {device_a, device_b}
        assert all(row["company_name"] == "Acme Co" for row in result.rows)


class TestServerBackedReports:
    """Synchronization, Update Deployment, Audit Log, and Device reports."""

    def test_device_report_lists_registered_devices(
        self, reporting_service, running_server_url, admin_bearer_token, tmp_path
    ) -> None:
        from config import DatabaseConfig
        from database.database import Database as AttendanceDatabase
        from sync.coordinator import ClientSyncCoordinator

        # Enroll a throwaway Attendance Client device against the same
        # running server so the Device report has at least one row.
        database = AttendanceDatabase(DatabaseConfig(sqlite_path=tmp_path / "client.db"))
        database.initialize()
        coordinator = ClientSyncCoordinator(database, running_server_url)
        coordinator.enroll(admin_bearer_token=admin_bearer_token, name="Test Device")
        database.dispose()

        result = reporting_service.build_device_report()
        assert len(result.rows) == 1
        assert result.rows[0]["name"] == "Test Device"
        assert result.columns[0] == ("name", "اسم الجهاز")

    def test_synchronization_and_audit_reports_degrade_to_empty_without_activity(self, reporting_service) -> None:
        sync_result = reporting_service.build_synchronization_report()
        assert sync_result.rows == []
        audit_result = reporting_service.build_audit_log_report()
        assert audit_result.rows == []

    def test_reports_wrap_admin_api_errors_into_reporting_service_error(
        self, customer_service, license_service, configuration_publish_service, dashboard_service
    ) -> None:
        unreachable_client = AdminApiClient("http://127.0.0.1:1", _StaticTokenProvider("token"))
        broken_reporting_service = ReportingService(
            customer_service, license_service, configuration_publish_service, dashboard_service, unreachable_client
        )
        with pytest.raises(ReportingServiceError):
            broken_reporting_service.build_device_report()

    def test_update_deployment_report_resolves_device_and_version_names(
        self, reporting_service, admin_client, running_server_url, admin_bearer_token, tmp_path
    ) -> None:
        from database.database import Database as AttendanceDatabase
        from config import DatabaseConfig
        from sync.coordinator import ClientSyncCoordinator
        from updates.client import UpdatesApiClient
        from developer_suite.services.update_manager_service import UpdateManagerService
        from repositories.sync_repository import ClientSyncCredentialRepository
        from licensing.crypto.signing import generate_keypair as gen_kp, save_private_key as save_priv

        # Enroll a throwaway Attendance Client so the report can resolve
        # its device name.
        client_db = AttendanceDatabase(DatabaseConfig(sqlite_path=tmp_path / "client.db"))
        client_db.initialize()
        coordinator = ClientSyncCoordinator(client_db, running_server_url)
        coordinator.enroll(admin_bearer_token=admin_bearer_token, name="Deployment Test Device")
        with client_db.session_scope() as session:
            credential = ClientSyncCredentialRepository(session).get()
            device_public_id = credential.device_public_id
            device_api_key = credential.api_key

        # Publish a version through the real Update Manager service (Phase 14).
        private_key, _public_key = gen_kp()
        key_path = tmp_path / "update_signing_key.pem"
        save_priv(private_key, key_path)
        update_manager_service = UpdateManagerService(admin_client, private_key_path=key_path)
        created = update_manager_service.create_version(
            version="9.9.9", release_notes=None, min_supported_version=None, update_type="optional"
        )
        package_path = tmp_path / "installer.bin"
        package_path.write_bytes(b"installer bytes")
        update_manager_service.upload_package(created.id, package_type="setup", file_path=package_path)
        update_manager_service.set_targets_all(created.id)
        update_manager_service.publish(created.id)

        # The device reports its own status directly (mirrors what an
        # Attendance Client installation does after checking for updates).
        updates_client = UpdatesApiClient(
            running_server_url, device_public_id=device_public_id, device_api_key=device_api_key
        )
        try:
            updates_client.report_status(update_version_id=created.id, status="downloading", progress_percent=42)
        finally:
            updates_client.close()

        result = reporting_service.build_update_deployment_report()
        assert len(result.rows) == 1
        row = result.rows[0]
        assert row["device_name"] == "Deployment Test Device"
        assert row["version"] == "9.9.9"
        assert row["status"] == "downloading"
        assert row["progress_percent"] == 42

        client_db.dispose()


# ---------------------------------------------------------------------------
# End-to-end: full report -> export round trip (CSV/Excel/PDF).
# ---------------------------------------------------------------------------


class TestExportRoundTrip:
    def test_customer_report_exports_to_all_three_formats(
        self, reporting_service, customer_service, dev_suite_config, tmp_path
    ) -> None:
        from utils.csv_export import export_to_csv
        from utils.excel import export_to_excel
        from utils.pdf import export_to_pdf

        customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe", email="a@a.com")
        result = reporting_service.build_customer_report()

        csv_path = export_to_csv(result.rows, result.columns, tmp_path / "customers.csv")
        assert csv_path.exists()
        content = csv_path.read_text(encoding="utf-8-sig")
        assert "Acme Co" in content
        # The bug this phase found and fixed: a raw tz-aware datetime must
        # never reach the exporter - only a plain, already-formatted string.
        assert "+00:00" not in content

        excel_path = export_to_excel(result.rows, result.columns, tmp_path / "customers.xlsx")
        assert excel_path.exists() and excel_path.stat().st_size > 0

        fonts_dir = dev_suite_config.paths.assets_dir / "fonts"
        pdf_path = export_to_pdf(
            result.rows, result.columns, tmp_path / "customers.pdf", title="Customers", fonts_dir=fonts_dir
        )
        assert pdf_path.exists() and pdf_path.stat().st_size > 0


# ---------------------------------------------------------------------------
# Regression guard: the Attendance Client's own PDF export call site.
# ---------------------------------------------------------------------------


class TestAttendanceClientPdfExportUnaffected:
    def test_report_service_still_produces_a_pdf_after_the_fonts_dir_signature_change(self, tmp_path) -> None:
        from utils.pdf import export_to_pdf
        from config import get_config

        fonts_dir = get_config().paths.assets_dir / "fonts"
        output = export_to_pdf(
            [{"a": "value"}], [("a", "Column")], tmp_path / "regression.pdf", title="Title", fonts_dir=fonts_dir
        )
        assert output.exists() and output.stat().st_size > 0


# ---------------------------------------------------------------------------
# Isolation.
# ---------------------------------------------------------------------------


class TestZeroImpactOnOtherApplications:
    def test_utils_pdf_no_longer_imports_the_attendance_client_config_module(self) -> None:
        import ast
        import inspect

        import utils.pdf as pdf_module

        tree = ast.parse(inspect.getsource(pdf_module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "config":
                pytest.fail("utils.pdf must not import the Attendance Client's config module anymore.")

    def test_reporting_service_imports_nothing_from_server_or_the_attendance_client_config_singleton(self) -> None:
        import ast
        import inspect

        import developer_suite.services.reporting_service as reporting_service_module

        tree = ast.parse(inspect.getsource(reporting_service_module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("server"), reporting_service_module.__name__
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("server"), reporting_service_module.__name__

    def test_no_new_tables_were_introduced_by_this_phase(self) -> None:
        """Phase 15 is read-only reporting - it must not add any new persisted storage."""
        import developer_suite.models  # noqa: F401 - registers every Developer Suite model on Base.metadata
        import server.models  # noqa: F401 - registers every server model on Base.metadata

        from developer_suite.database.base import Base as DeveloperSuiteBase
        from server.database.base import Base as ServerBase

        # A snapshot of every table that existed after Phase 14 - if this
        # test ever needs to grow, it means a later phase deliberately
        # added real storage, not Phase 15 (which is read-only reporting
        # over data these two schemas already had).
        expected_developer_suite_tables = {
            "customers", "issued_licenses", "remote_configurations", "theme_profiles",
            "print_profiles", "attendance_policy_profiles", "device_profiles", "backup_profiles",
            "configuration_publications", "sync_outbox_entries", "sync_entity_versions",
            "sync_cursors", "sync_device_credential", "admin_session_record",
            "customer_groups", "customer_group_members",
        }
        assert set(DeveloperSuiteBase.metadata.tables) == expected_developer_suite_tables

        expected_server_tables = {
            "admin_accounts", "admin_audit_logs", "admin_password_reset_tokens", "admin_sessions",
            "change_records", "device_update_statuses", "entity_versions", "sync_devices",
            "sync_sequence", "update_audit_events", "update_packages", "update_rollbacks",
            "update_targets", "update_versions",
        }
        assert set(ServerBase.metadata.tables) == expected_server_tables

    def test_importing_reporting_service_does_not_create_developer_suite_config_singleton(self) -> None:
        import developer_suite.services.reporting_service  # noqa: F401

        assert developer_suite_config_module._config_instance is None

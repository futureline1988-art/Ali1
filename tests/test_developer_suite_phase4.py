"""Tests for Phase 4 of the commercial platform work: License Manager
foundation inside the Developer Suite.

Every test here exercises only :mod:`developer_suite`; nothing touches
the Attendance Client's own database, config, models, or the shipped
``licensing/keys.py`` public key. Signing uses a throwaway Ed25519
keypair generated per test run (via
:mod:`licensing.crypto.signing`, Phase 1's foundation code) — see
:class:`~developer_suite.services.license_service.LicenseService`'s
docstring for why the service verifies against the *same* keypair it
signed with rather than the real shipped public key.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import developer_suite.config as developer_suite_config_module
from developer_suite.config import DeveloperSuiteConfig, get_developer_suite_config
from developer_suite.database.bootstrap import build_database
from developer_suite.models.customer import Customer
from developer_suite.models.license import IssuedLicense, IssuedLicenseStatus
from developer_suite.repositories.license_repository import LicenseRepository
from developer_suite.services.customer_service import CustomerNotFoundError, CustomerService
from developer_suite.services.license_service import (
    LicenseNotFoundError,
    LicenseService,
    LicenseSigningKeyError,
)
from developer_suite.ui.license_form_dialog import LicenseFormDialog
from developer_suite.ui.license_management_page import LicenseManagementPage
from licensing.crypto.signing import generate_keypair, save_private_key
from licensing.enums import LicenseType
from licensing.license_key import decode_and_verify_license_key


@pytest.fixture
def dev_suite_config(tmp_path, monkeypatch) -> DeveloperSuiteConfig:
    monkeypatch.setenv("DEV_SUITE_DB_SQLITE_PATH", str(tmp_path / "developer_suite_test.db"))
    developer_suite_config_module._config_instance = None
    yield get_developer_suite_config()
    developer_suite_config_module._config_instance = None


@pytest.fixture
def dev_suite_database(dev_suite_config):
    database = build_database(dev_suite_config)
    yield database
    database.dispose()


@pytest.fixture
def customer_service(dev_suite_database) -> CustomerService:
    return CustomerService(dev_suite_database)


@pytest.fixture
def customer(customer_service: CustomerService) -> Customer:
    return customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")


@pytest.fixture
def private_key_path(tmp_path) -> Path:
    """A throwaway signing keypair, saved to disk, for one test."""
    key_path = tmp_path / "keys" / "license_private_key.pem"
    private_key, _public_key = generate_keypair()
    save_private_key(private_key, key_path)
    return key_path


@pytest.fixture
def license_service(dev_suite_database, private_key_path: Path) -> LicenseService:
    return LicenseService(dev_suite_database, private_key_path=private_key_path)


class TestIssuedLicenseModel:
    def test_defaults_to_active(self, dev_suite_database, customer: Customer) -> None:
        with dev_suite_database.session_scope() as session:
            record = IssuedLicense(
                customer_id=customer.id,
                license_type=LicenseType.MONTHLY,
                license_key="AMS1.x.y",
                issued_at=date.today(),
                expires_at=date.today() + timedelta(days=30),
            )
            LicenseRepository(session).add(record)
            assert record.status is IssuedLicenseStatus.ACTIVE
            assert record.is_active is True

    def test_is_expired_true_for_past_expiry(self, dev_suite_database, customer: Customer) -> None:
        with dev_suite_database.session_scope() as session:
            record = IssuedLicense(
                customer_id=customer.id,
                license_type=LicenseType.MONTHLY,
                license_key="AMS1.x.y",
                issued_at=date.today() - timedelta(days=60),
                expires_at=date.today() - timedelta(days=1),
            )
            LicenseRepository(session).add(record)
            assert record.is_expired is True
            assert record.is_active is False

    def test_days_remaining_is_none_for_lifetime(self, dev_suite_database, customer: Customer) -> None:
        with dev_suite_database.session_scope() as session:
            record = IssuedLicense(
                customer_id=customer.id,
                license_type=LicenseType.LIFETIME,
                license_key="AMS1.x.y",
                issued_at=date.today(),
                expires_at=None,
            )
            LicenseRepository(session).add(record)
            assert record.days_remaining is None
            assert record.is_expired is False


class TestLicenseRepository:
    def test_add_and_get_by_id_eager_loads_customer(
        self, dev_suite_database, customer: Customer
    ) -> None:
        with dev_suite_database.session_scope() as session:
            repo = LicenseRepository(session)
            record = repo.add(
                IssuedLicense(
                    customer_id=customer.id,
                    license_type=LicenseType.YEARLY,
                    license_key="AMS1.x.y",
                    issued_at=date.today(),
                    expires_at=date.today() + timedelta(days=365),
                )
            )
            license_id = record.id

        with dev_suite_database.session_scope() as session:
            fetched = LicenseRepository(session).get_by_id(license_id)
            assert fetched is not None
        # Accessed after the session that fetched it is closed - proves eager loading.
        assert fetched.customer.company_name == "Acme Co"

    def test_search_matches_company_name(self, dev_suite_database, customer_service) -> None:
        acme = customer_service.create_customer(company_name="Acme Corporation", contact_name="Alice")
        widgets = customer_service.create_customer(company_name="Widgets Inc", contact_name="Bob")
        with dev_suite_database.session_scope() as session:
            repo = LicenseRepository(session)
            repo.add(
                IssuedLicense(
                    customer_id=acme.id,
                    license_type=LicenseType.TRIAL,
                    license_key="AMS1.a.a",
                    issued_at=date.today(),
                    expires_at=date.today() + timedelta(days=14),
                )
            )
            repo.add(
                IssuedLicense(
                    customer_id=widgets.id,
                    license_type=LicenseType.TRIAL,
                    license_key="AMS1.b.b",
                    issued_at=date.today(),
                    expires_at=date.today() + timedelta(days=14),
                )
            )

        with dev_suite_database.session_scope() as session:
            results = LicenseRepository(session).search("acme")
        assert len(results) == 1
        assert results[0].customer.company_name == "Acme Corporation"

    def test_search_matches_machine_id(self, dev_suite_database, customer: Customer) -> None:
        with dev_suite_database.session_scope() as session:
            LicenseRepository(session).add(
                IssuedLicense(
                    customer_id=customer.id,
                    license_type=LicenseType.TRIAL,
                    license_key="AMS1.a.a",
                    machine_id="ABC123",
                    issued_at=date.today(),
                    expires_at=date.today() + timedelta(days=14),
                )
            )

        with dev_suite_database.session_scope() as session:
            assert len(LicenseRepository(session).search("abc123")) == 1
            assert len(LicenseRepository(session).search("nomatch")) == 0

    def test_empty_search_returns_all(self, dev_suite_database, customer: Customer) -> None:
        with dev_suite_database.session_scope() as session:
            repo = LicenseRepository(session)
            for _ in range(2):
                repo.add(
                    IssuedLicense(
                        customer_id=customer.id,
                        license_type=LicenseType.TRIAL,
                        license_key=f"AMS1.{_}.{_}",
                        issued_at=date.today(),
                        expires_at=date.today() + timedelta(days=14),
                    )
                )

        with dev_suite_database.session_scope() as session:
            assert len(LicenseRepository(session).search("")) == 2

    def test_soft_deleted_excluded_from_search(self, dev_suite_database, customer: Customer) -> None:
        with dev_suite_database.session_scope() as session:
            repo = LicenseRepository(session)
            record = repo.add(
                IssuedLicense(
                    customer_id=customer.id,
                    license_type=LicenseType.TRIAL,
                    license_key="AMS1.a.a",
                    issued_at=date.today(),
                    expires_at=date.today() + timedelta(days=14),
                )
            )
            repo.delete(record)

        with dev_suite_database.session_scope() as session:
            assert LicenseRepository(session).search("") == []

    def test_list_by_customer_returns_only_that_customers_licenses(
        self, dev_suite_database, customer_service
    ) -> None:
        acme = customer_service.create_customer(company_name="Acme Corporation", contact_name="Alice")
        widgets = customer_service.create_customer(company_name="Widgets Inc", contact_name="Bob")
        with dev_suite_database.session_scope() as session:
            repo = LicenseRepository(session)
            repo.add(
                IssuedLicense(
                    customer_id=acme.id,
                    license_type=LicenseType.TRIAL,
                    license_key="AMS1.a.a",
                    issued_at=date.today(),
                    expires_at=date.today() + timedelta(days=14),
                )
            )
            repo.add(
                IssuedLicense(
                    customer_id=widgets.id,
                    license_type=LicenseType.TRIAL,
                    license_key="AMS1.b.b",
                    issued_at=date.today(),
                    expires_at=date.today() + timedelta(days=14),
                )
            )

        with dev_suite_database.session_scope() as session:
            results = LicenseRepository(session).list_by_customer(acme.id)
        assert len(results) == 1
        assert results[0].customer_id == acme.id


class TestLicenseService:
    def test_issue_license_creates_active_license(
        self, license_service: LicenseService, customer: Customer
    ) -> None:
        record = license_service.issue_license(customer_id=customer.id, license_type=LicenseType.YEARLY)
        assert record.id is not None
        assert record.status is IssuedLicenseStatus.ACTIVE
        assert record.customer.company_name == "Acme Co"
        assert record.license_key.startswith("AMS1.")

    def test_issue_license_raises_for_unknown_customer(self, license_service: LicenseService) -> None:
        with pytest.raises(CustomerNotFoundError):
            license_service.issue_license(customer_id=999999, license_type=LicenseType.TRIAL)

    def test_issue_license_raises_when_signing_key_missing(
        self, dev_suite_database, customer: Customer, tmp_path
    ) -> None:
        service = LicenseService(dev_suite_database, private_key_path=tmp_path / "missing.pem")
        with pytest.raises(LicenseSigningKeyError):
            service.issue_license(customer_id=customer.id, license_type=LicenseType.TRIAL)

    def test_issue_license_computes_expiry_by_type(
        self, license_service: LicenseService, customer: Customer
    ) -> None:
        monthly = license_service.issue_license(customer_id=customer.id, license_type=LicenseType.MONTHLY)
        assert (monthly.expires_at - monthly.issued_at).days == 30

        lifetime = license_service.issue_license(
            customer_id=customer.id, license_type=LicenseType.LIFETIME
        )
        assert lifetime.expires_at is None

    def test_issue_license_with_days_override(
        self, license_service: LicenseService, customer: Customer
    ) -> None:
        record = license_service.issue_license(
            customer_id=customer.id, license_type=LicenseType.MONTHLY, days=7
        )
        assert (record.expires_at - record.issued_at).days == 7

    def test_issue_license_key_decodes_and_matches_customer(
        self, license_service: LicenseService, customer: Customer, private_key_path: Path
    ) -> None:
        record = license_service.issue_license(
            customer_id=customer.id,
            license_type=LicenseType.YEARLY,
            machine_id="MACHINE-1",
            licensed_version="1.2.0",
        )
        from licensing.crypto.signing import load_private_key

        public_key = load_private_key(private_key_path).public_key()
        payload = decode_and_verify_license_key(record.license_key, public_key)
        assert payload.customer_name == "Jane Doe"
        assert payload.company_name == "Acme Co"
        assert payload.license_type is LicenseType.YEARLY
        assert payload.machine_id == "MACHINE-1"
        assert payload.licensed_version == "1.2.0"

    def test_renew_license_extends_expiry_and_reactivates(
        self, license_service: LicenseService, customer: Customer
    ) -> None:
        record = license_service.issue_license(customer_id=customer.id, license_type=LicenseType.TRIAL)
        license_service.revoke_license(record.id)

        renewed = license_service.renew_license(record.id)
        assert renewed.status is IssuedLicenseStatus.ACTIVE
        assert renewed.issued_at == date.today()

    def test_renew_license_raises_for_unknown_id(self, license_service: LicenseService) -> None:
        with pytest.raises(LicenseNotFoundError):
            license_service.renew_license(999999)

    def test_revoke_license_sets_status_revoked(
        self, license_service: LicenseService, customer: Customer
    ) -> None:
        record = license_service.issue_license(customer_id=customer.id, license_type=LicenseType.TRIAL)
        revoked = license_service.revoke_license(record.id)
        assert revoked.status is IssuedLicenseStatus.REVOKED
        assert revoked.is_active is False

    def test_revoke_license_raises_for_unknown_id(self, license_service: LicenseService) -> None:
        with pytest.raises(LicenseNotFoundError):
            license_service.revoke_license(999999)

    def test_get_license_returns_none_for_unknown_id(self, license_service: LicenseService) -> None:
        assert license_service.get_license(999999) is None

    def test_search_licenses_finds_by_company_name(
        self, license_service: LicenseService, customer_service
    ) -> None:
        acme = customer_service.create_customer(company_name="Acme Corporation", contact_name="Alice")
        widgets = customer_service.create_customer(company_name="Widgets Inc", contact_name="Bob")
        license_service.issue_license(customer_id=acme.id, license_type=LicenseType.TRIAL)
        license_service.issue_license(customer_id=widgets.id, license_type=LicenseType.TRIAL)

        results = license_service.search_licenses("widgets")
        assert len(results) == 1
        assert results[0].customer.company_name == "Widgets Inc"

    def test_list_by_customer(self, license_service: LicenseService, customer: Customer) -> None:
        license_service.issue_license(customer_id=customer.id, license_type=LicenseType.TRIAL)
        license_service.issue_license(customer_id=customer.id, license_type=LicenseType.YEARLY)
        assert len(license_service.list_by_customer(customer.id)) == 2


class TestLicenseFormDialog:
    def test_defaults_use_first_customer_and_first_license_type(
        self, qapp, customer: Customer
    ) -> None:
        dialog = LicenseFormDialog(customers=[customer])
        values = dialog.field_values()
        assert values["customer_id"] == customer.id
        assert values["license_type"] is LicenseType.TRIAL
        assert values["machine_id"] is None
        assert values["licensed_version"] is None
        assert values["days"] is None

    def test_filled_optional_fields_are_returned(self, qapp, customer: Customer) -> None:
        dialog = LicenseFormDialog(customers=[customer])
        dialog.machine_id_edit.setText("MACHINE-1")
        dialog.licensed_version_edit.setText("1.2.0")
        dialog.days_override_spin.setValue(45)
        values = dialog.field_values()
        assert values["machine_id"] == "MACHINE-1"
        assert values["licensed_version"] == "1.2.0"
        assert values["days"] == 45


class TestLicenseManagementPage:
    def test_loads_existing_licenses_on_construction(
        self, qapp, license_service: LicenseService, customer_service, customer: Customer
    ) -> None:
        license_service.issue_license(customer_id=customer.id, license_type=LicenseType.TRIAL)
        license_service.issue_license(customer_id=customer.id, license_type=LicenseType.YEARLY)

        page = LicenseManagementPage(license_service, customer_service)
        assert page.table.rowCount() == 2

    def test_search_filters_the_table(
        self, qapp, license_service: LicenseService, customer_service
    ) -> None:
        acme = customer_service.create_customer(company_name="Acme Corporation", contact_name="Alice")
        widgets = customer_service.create_customer(company_name="Widgets Inc", contact_name="Bob")
        license_service.issue_license(customer_id=acme.id, license_type=LicenseType.TRIAL)
        license_service.issue_license(customer_id=widgets.id, license_type=LicenseType.TRIAL)

        page = LicenseManagementPage(license_service, customer_service)
        page.search_edit.setText("Acme")
        assert page.table.rowCount() == 1
        assert page.table.item(0, 0).text() == "Acme Corporation"

    def test_selected_license_returns_none_without_selection(
        self, qapp, license_service: LicenseService, customer_service
    ) -> None:
        page = LicenseManagementPage(license_service, customer_service)
        assert page._selected_license() is None

    def test_selected_license_matches_current_row(
        self, qapp, license_service: LicenseService, customer_service, customer: Customer
    ) -> None:
        license_service.issue_license(customer_id=customer.id, license_type=LicenseType.TRIAL)
        page = LicenseManagementPage(license_service, customer_service)
        page.table.selectRow(0)
        selected = page._selected_license()
        assert selected is not None
        assert selected.customer.company_name == "Acme Co"

    def test_reload_reflects_revocation(
        self, qapp, license_service: LicenseService, customer_service, customer: Customer
    ) -> None:
        record = license_service.issue_license(customer_id=customer.id, license_type=LicenseType.TRIAL)
        page = LicenseManagementPage(license_service, customer_service)
        assert page.table.item(0, 5).text() == "نشط"

        license_service.revoke_license(record.id)
        page.reload()
        assert page.table.item(0, 5).text() == "ملغى"


class TestZeroImpactOnAttendanceClient:
    def test_license_table_lives_only_in_developer_suite_schema(self) -> None:
        from developer_suite.database.base import Base as DeveloperSuiteBase
        from models.base import Base as AttendanceBase

        assert "issued_licenses" in DeveloperSuiteBase.metadata.tables
        assert "issued_licenses" not in AttendanceBase.metadata.tables

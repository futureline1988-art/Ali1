"""Tests for Phase 3 of the commercial platform work: Customer Management
foundation inside the Developer Suite.

Every test here exercises only :mod:`developer_suite`; nothing touches
the Attendance Client's own database, config, or models.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import developer_suite.config as developer_suite_config_module
from developer_suite.config import DeveloperSuiteConfig, get_developer_suite_config
from developer_suite.database.bootstrap import build_database
from developer_suite.models.customer import Customer, CustomerStatus
from developer_suite.repositories.customer_repository import CustomerRepository
from developer_suite.services.customer_service import (
    CustomerNotFoundError,
    CustomerService,
    CustomerValidationError,
)
from developer_suite.services.license_service import LicenseService
from developer_suite.sync.coordinator import SyncCoordinator
from developer_suite.sync.customer_sync import register_customer_sync
from developer_suite.ui.customer_form_dialog import CustomerFormDialog
from developer_suite.ui.customer_management_page import CustomerManagementPage


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
def license_service(dev_suite_database, tmp_path) -> LicenseService:
    return LicenseService(dev_suite_database, private_key_path=tmp_path / "nonexistent.pem")


@pytest.fixture
def sync_coordinator(dev_suite_database, dev_suite_config) -> SyncCoordinator:
    coordinator = SyncCoordinator(dev_suite_database, dev_suite_config)
    register_customer_sync(coordinator)
    return coordinator


def _build_page(customer_service, license_service, sync_coordinator) -> CustomerManagementPage:
    """Construct a page with every dependency it now requires (Phase 10 added two)."""
    return CustomerManagementPage(customer_service, license_service, sync_coordinator)


class TestCustomerModel:
    def test_defaults_to_active(self, dev_suite_database) -> None:
        with dev_suite_database.session_scope() as session:
            customer = Customer(company_name="Acme Co", contact_name="Jane Doe")
            CustomerRepository(session).add(customer)
            assert customer.status is CustomerStatus.ACTIVE
            assert customer.is_active is True

    def test_public_id_is_assigned(self, dev_suite_database) -> None:
        with dev_suite_database.session_scope() as session:
            customer = Customer(company_name="Acme Co", contact_name="Jane Doe")
            CustomerRepository(session).add(customer)
            assert customer.public_id is not None


class TestCustomerRepository:
    def test_add_and_get_by_id(self, dev_suite_database) -> None:
        with dev_suite_database.session_scope() as session:
            repo = CustomerRepository(session)
            customer = repo.add(Customer(company_name="Acme Co", contact_name="Jane Doe"))
            fetched = repo.get_by_id(customer.id)
            assert fetched is not None
            assert fetched.company_name == "Acme Co"

    def test_search_matches_company_name_case_insensitively(self, dev_suite_database) -> None:
        with dev_suite_database.session_scope() as session:
            repo = CustomerRepository(session)
            repo.add(Customer(company_name="Acme Corporation", contact_name="Jane Doe"))
            repo.add(Customer(company_name="Widgets Inc", contact_name="John Roe"))

            results = repo.search("acme")
            assert len(results) == 1
            assert results[0].company_name == "Acme Corporation"

    def test_search_matches_contact_name(self, dev_suite_database) -> None:
        with dev_suite_database.session_scope() as session:
            repo = CustomerRepository(session)
            repo.add(Customer(company_name="Acme Corporation", contact_name="Jane Doe"))
            repo.add(Customer(company_name="Widgets Inc", contact_name="John Roe"))

            results = repo.search("roe")
            assert len(results) == 1
            assert results[0].contact_name == "John Roe"

    def test_empty_search_returns_all(self, dev_suite_database) -> None:
        with dev_suite_database.session_scope() as session:
            repo = CustomerRepository(session)
            repo.add(Customer(company_name="Acme Corporation", contact_name="Jane Doe"))
            repo.add(Customer(company_name="Widgets Inc", contact_name="John Roe"))

            assert len(repo.search("")) == 2

    def test_soft_deleted_excluded_from_search(self, dev_suite_database) -> None:
        with dev_suite_database.session_scope() as session:
            repo = CustomerRepository(session)
            customer = repo.add(Customer(company_name="Acme Corporation", contact_name="Jane Doe"))
            repo.delete(customer)

            assert repo.search("") == []
            assert repo.search("", include_deleted=True) != []


class TestCustomerService:
    def test_create_customer_returns_active_customer(self, customer_service: CustomerService) -> None:
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        assert customer.id is not None
        assert customer.status is CustomerStatus.ACTIVE

    def test_create_customer_strips_whitespace(self, customer_service: CustomerService) -> None:
        customer = customer_service.create_customer(
            company_name="  Acme Co  ", contact_name="  Jane Doe  "
        )
        assert customer.company_name == "Acme Co"
        assert customer.contact_name == "Jane Doe"

    def test_create_customer_rejects_short_company_name(self, customer_service: CustomerService) -> None:
        with pytest.raises(CustomerValidationError):
            customer_service.create_customer(company_name="A", contact_name="Jane Doe")

    def test_create_customer_rejects_invalid_email(self, customer_service: CustomerService) -> None:
        with pytest.raises(CustomerValidationError):
            customer_service.create_customer(
                company_name="Acme Co", contact_name="Jane Doe", email="not-an-email"
            )

    def test_create_customer_rejects_invalid_phone(self, customer_service: CustomerService) -> None:
        with pytest.raises(CustomerValidationError):
            customer_service.create_customer(
                company_name="Acme Co", contact_name="Jane Doe", phone="not-a-phone!!"
            )

    def test_create_customer_accepts_valid_email_and_phone(
        self, customer_service: CustomerService
    ) -> None:
        customer = customer_service.create_customer(
            company_name="Acme Co",
            contact_name="Jane Doe",
            email="jane@acme.example",
            phone="+15551234567",
        )
        assert customer.email == "jane@acme.example"

    def test_update_customer_changes_fields(self, customer_service: CustomerService) -> None:
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        updated = customer_service.update_customer(
            customer.id, company_name="Acme Corp", contact_name="Jane Roe"
        )
        assert updated.company_name == "Acme Corp"
        assert updated.contact_name == "Jane Roe"

    def test_update_customer_raises_for_unknown_id(self, customer_service: CustomerService) -> None:
        with pytest.raises(CustomerNotFoundError):
            customer_service.update_customer(999999, company_name="Acme Co", contact_name="Jane Doe")

    def test_delete_customer_removes_it_from_search(self, customer_service: CustomerService) -> None:
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        customer_service.delete_customer(customer.id)
        assert customer_service.get_customer(customer.id) is None

    def test_delete_customer_raises_for_unknown_id(self, customer_service: CustomerService) -> None:
        with pytest.raises(CustomerNotFoundError):
            customer_service.delete_customer(999999)

    def test_search_customers_finds_by_partial_name(self, customer_service: CustomerService) -> None:
        customer_service.create_customer(company_name="Acme Corporation", contact_name="Jane Doe")
        customer_service.create_customer(company_name="Widgets Inc", contact_name="John Roe")

        results = customer_service.search_customers("widgets")
        assert len(results) == 1
        assert results[0].company_name == "Widgets Inc"

    def test_suspend_then_reactivate(self, customer_service: CustomerService) -> None:
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")

        suspended = customer_service.suspend(customer.id)
        assert suspended.status is CustomerStatus.SUSPENDED
        assert suspended.is_active is False

        reactivated = customer_service.reactivate(customer.id)
        assert reactivated.status is CustomerStatus.ACTIVE
        assert reactivated.is_active is True

    def test_suspend_raises_for_unknown_id(self, customer_service: CustomerService) -> None:
        with pytest.raises(CustomerNotFoundError):
            customer_service.suspend(999999)

    def test_get_customer_returns_none_for_unknown_id(self, customer_service: CustomerService) -> None:
        assert customer_service.get_customer(999999) is None


class TestCustomerFormDialog:
    def test_add_mode_starts_blank(self, qapp) -> None:
        dialog = CustomerFormDialog(existing=None)
        values = dialog.field_values()
        assert values["company_name"] == ""
        assert values["phone"] is None

    def test_edit_mode_prefills_fields(self, qapp, customer_service: CustomerService) -> None:
        customer = customer_service.create_customer(
            company_name="Acme Co", contact_name="Jane Doe", phone="+15551234567", email="a@b.com"
        )
        dialog = CustomerFormDialog(existing=customer)
        values = dialog.field_values()
        assert values["company_name"] == "Acme Co"
        assert values["contact_name"] == "Jane Doe"
        assert values["phone"] == "+15551234567"
        assert values["email"] == "a@b.com"

    def test_field_values_blank_optional_fields_are_none(self, qapp) -> None:
        dialog = CustomerFormDialog(existing=None)
        dialog.company_name_edit.setText("Acme Co")
        dialog.contact_name_edit.setText("Jane Doe")
        values = dialog.field_values()
        assert values["phone"] is None
        assert values["email"] is None
        assert values["address"] is None
        assert values["notes"] is None


class TestCustomerManagementPage:
    def test_loads_existing_customers_on_construction(
        self, qapp, customer_service: CustomerService, license_service, sync_coordinator
    ) -> None:
        customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        customer_service.create_customer(company_name="Widgets Inc", contact_name="John Roe")

        page = _build_page(customer_service, license_service, sync_coordinator)
        assert page.table.rowCount() == 2

    def test_search_filters_the_table(
        self, qapp, customer_service: CustomerService, license_service, sync_coordinator
    ) -> None:
        customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        customer_service.create_customer(company_name="Widgets Inc", contact_name="John Roe")

        page = _build_page(customer_service, license_service, sync_coordinator)
        page.search_edit.setText("Acme")
        assert page.table.rowCount() == 1
        assert page.table.item(0, 0).text() == "Acme Co"

    def test_selected_customer_returns_none_without_selection(
        self, qapp, customer_service: CustomerService, license_service, sync_coordinator
    ) -> None:
        page = _build_page(customer_service, license_service, sync_coordinator)
        assert page._selected_customer() is None

    def test_selected_customer_matches_current_row(
        self, qapp, customer_service: CustomerService, license_service, sync_coordinator
    ) -> None:
        customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        page = _build_page(customer_service, license_service, sync_coordinator)
        page.table.selectRow(0)
        selected = page._selected_customer()
        assert selected is not None
        assert selected.company_name == "Acme Co"

    def test_reload_reflects_status_change(
        self, qapp, customer_service: CustomerService, license_service, sync_coordinator
    ) -> None:
        customer = customer_service.create_customer(company_name="Acme Co", contact_name="Jane Doe")
        page = _build_page(customer_service, license_service, sync_coordinator)
        assert page.table.item(0, 4).text() == "نشط"

        customer_service.suspend(customer.id)
        page.reload()
        assert page.table.item(0, 4).text() == "موقوف"


class TestZeroImpactOnAttendanceClient:
    def test_customer_table_lives_only_in_developer_suite_schema(self) -> None:
        from developer_suite.database.base import Base as DeveloperSuiteBase
        from models.base import Base as AttendanceBase

        assert "customers" in DeveloperSuiteBase.metadata.tables
        assert "customers" not in AttendanceBase.metadata.tables

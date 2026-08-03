"""Customer registry business logic.

Reuses ``utils.validators`` directly (``is_valid_email``,
``is_valid_phone``, ``is_within_length``) — genuinely dependency-free,
stdlib-only helpers already documented as shared between the desktop
UI and service layers, exactly the kind of shared library this
platform's design rules call for reusing rather than reimplementing.
"""

from __future__ import annotations

from developer_suite.models.customer import Customer, CustomerStatus
from developer_suite.repositories.customer_repository import CustomerRepository
from developer_suite.services.base_service import BaseService
from utils.validators import is_valid_email, is_valid_phone, is_within_length


class CustomerServiceError(Exception):
    """Base class for customer operation failures the UI should display."""


class CustomerValidationError(CustomerServiceError):
    """A field failed validation."""


class CustomerNotFoundError(CustomerServiceError):
    """No customer exists with the given id."""


class CustomerService(BaseService):
    """Create, update, search, suspend, and reactivate customer records."""

    def _validate(
        self,
        *,
        company_name: str,
        contact_name: str,
        phone: str | None,
        email: str | None,
    ) -> None:
        """Validate customer field shapes, raising on the first failure.

        Args:
            company_name: The customer's company/trade name.
            contact_name: The primary contact person's name.
            phone: Contact phone number, if given.
            email: Contact email address, if given.

        Raises:
            CustomerValidationError: A field fails validation.
        """
        if not is_within_length(company_name, minimum=2, maximum=200):
            raise CustomerValidationError("Company name must be 2-200 characters.")
        if not is_within_length(contact_name, minimum=2, maximum=200):
            raise CustomerValidationError("Contact name must be 2-200 characters.")
        if phone and not is_valid_phone(phone):
            raise CustomerValidationError(f"{phone!r} is not a valid phone number.")
        if email and not is_valid_email(email):
            raise CustomerValidationError(f"{email!r} is not a valid email address.")

    def create_customer(
        self,
        *,
        company_name: str,
        contact_name: str,
        phone: str | None = None,
        email: str | None = None,
        address: str | None = None,
        notes: str | None = None,
    ) -> Customer:
        """Register a new customer.

        Args:
            company_name: The customer's company/trade name.
            contact_name: The primary contact person's name.
            phone: Contact phone number.
            email: Contact email address.
            address: Postal/street address.
            notes: Free-form vendor notes.

        Returns:
            The newly created customer, active by default.

        Raises:
            CustomerValidationError: A field fails validation.
        """
        self._validate(company_name=company_name, contact_name=contact_name, phone=phone, email=email)

        with self._session_scope() as session:
            customer = Customer(
                company_name=company_name.strip(),
                contact_name=contact_name.strip(),
                phone=phone.strip() if phone else None,
                email=email.strip() if email else None,
                address=address.strip() if address else None,
                notes=notes.strip() if notes else None,
                status=CustomerStatus.ACTIVE,
            )
            CustomerRepository(session).add(customer)
            return customer

    def update_customer(
        self,
        customer_id: int,
        *,
        company_name: str,
        contact_name: str,
        phone: str | None = None,
        email: str | None = None,
        address: str | None = None,
        notes: str | None = None,
    ) -> Customer:
        """Update an existing customer's details (not its status — see :meth:`suspend`/:meth:`reactivate`).

        Args:
            customer_id: The customer to update.
            company_name: The customer's company/trade name.
            contact_name: The primary contact person's name.
            phone: Contact phone number.
            email: Contact email address.
            address: Postal/street address.
            notes: Free-form vendor notes.

        Returns:
            The updated customer.

        Raises:
            CustomerValidationError: A field fails validation.
            CustomerNotFoundError: No customer exists with that id.
        """
        self._validate(company_name=company_name, contact_name=contact_name, phone=phone, email=email)

        with self._session_scope() as session:
            customer = CustomerRepository(session).get_by_id(customer_id)
            if customer is None:
                raise CustomerNotFoundError(f"No customer with id={customer_id!r}.")

            customer.company_name = company_name.strip()
            customer.contact_name = contact_name.strip()
            customer.phone = phone.strip() if phone else None
            customer.email = email.strip() if email else None
            customer.address = address.strip() if address else None
            customer.notes = notes.strip() if notes else None
            session.flush()
            return customer

    def delete_customer(self, customer_id: int) -> None:
        """Soft-delete a customer.

        Args:
            customer_id: The customer to delete.

        Raises:
            CustomerNotFoundError: No customer exists with that id.
        """
        with self._session_scope() as session:
            repo = CustomerRepository(session)
            customer = repo.get_by_id(customer_id)
            if customer is None:
                raise CustomerNotFoundError(f"No customer with id={customer_id!r}.")
            repo.delete(customer)

    def get_customer(self, customer_id: int) -> Customer | None:
        """Fetch a single customer by id.

        Args:
            customer_id: The customer to fetch.

        Returns:
            The matching customer, or ``None`` if not found (or
            soft-deleted).
        """
        with self._session_scope() as session:
            return CustomerRepository(session).get_by_id(customer_id)

    def search_customers(self, query: str = "") -> list[Customer]:
        """Search customers by company or contact name.

        Args:
            query: A case-insensitive substring; empty returns every
                customer.

        Returns:
            Matching customers, ordered by company name.
        """
        with self._session_scope() as session:
            return CustomerRepository(session).search(query)

    def _set_status(self, customer_id: int, status: CustomerStatus) -> Customer:
        """Shared implementation for :meth:`suspend`/:meth:`reactivate`."""
        with self._session_scope() as session:
            customer = CustomerRepository(session).get_by_id(customer_id)
            if customer is None:
                raise CustomerNotFoundError(f"No customer with id={customer_id!r}.")
            customer.status = status
            session.flush()
            return customer

    def suspend(self, customer_id: int) -> Customer:
        """Suspend a customer's account.

        Args:
            customer_id: The customer to suspend.

        Returns:
            The updated customer.

        Raises:
            CustomerNotFoundError: No customer exists with that id.
        """
        return self._set_status(customer_id, CustomerStatus.SUSPENDED)

    def reactivate(self, customer_id: int) -> Customer:
        """Reactivate a suspended customer's account.

        Args:
            customer_id: The customer to reactivate.

        Returns:
            The updated customer.

        Raises:
            CustomerNotFoundError: No customer exists with that id.
        """
        return self._set_status(customer_id, CustomerStatus.ACTIVE)

"""Customer group management: create/rename/delete a group and manage its members.

A small, standalone service rather than folded into
:class:`~developer_suite.services.customer_service.CustomerService`,
since groups are a Phase 14-only, update-targeting convenience (see
:mod:`developer_suite.models.customer_group`'s own docstring) with no
relationship to anything customer *lifecycle* (onboarding, licensing,
suspension) already owns.
"""

from __future__ import annotations

from developer_suite.models.customer import Customer
from developer_suite.models.customer_group import CustomerGroup
from developer_suite.repositories.customer_group_repository import CustomerGroupRepository
from developer_suite.repositories.customer_repository import CustomerRepository
from developer_suite.services.base_service import BaseService
from utils.validators import is_within_length


class CustomerGroupServiceError(Exception):
    """Base class for customer-group operation failures the UI should display."""


class CustomerGroupValidationError(CustomerGroupServiceError):
    """A field failed validation."""


class CustomerGroupNotFoundError(CustomerGroupServiceError):
    """No group exists with the given id."""


class CustomerGroupService(BaseService):
    """Create, rename, delete, and manage membership of customer groups."""

    def create_group(self, *, name: str) -> CustomerGroup:
        """Create a new, empty group.

        Raises:
            CustomerGroupValidationError: ``name`` fails validation.
        """
        if not is_within_length(name, minimum=2, maximum=150):
            raise CustomerGroupValidationError("Name must be 2-150 characters.")
        with self._session_scope() as session:
            return CustomerGroupRepository(session).add(CustomerGroup(name=name.strip()))

    def rename_group(self, group_id: int, *, name: str) -> CustomerGroup:
        """Rename an existing group.

        Raises:
            CustomerGroupValidationError: ``name`` fails validation.
            CustomerGroupNotFoundError: No group exists with that id.
        """
        if not is_within_length(name, minimum=2, maximum=150):
            raise CustomerGroupValidationError("Name must be 2-150 characters.")
        with self._session_scope() as session:
            group = CustomerGroupRepository(session).get_by_id(group_id)
            if group is None:
                raise CustomerGroupNotFoundError(f"No customer group with id={group_id!r}.")
            group.name = name.strip()
            session.flush()
            return group

    def delete_group(self, group_id: int) -> None:
        """Soft-delete a group; its member customers are entirely unaffected.

        Raises:
            CustomerGroupNotFoundError: No group exists with that id.
        """
        with self._session_scope() as session:
            repo = CustomerGroupRepository(session)
            group = repo.get_by_id(group_id)
            if group is None:
                raise CustomerGroupNotFoundError(f"No customer group with id={group_id!r}.")
            repo.delete(group)

    def set_members(self, group_id: int, *, customer_ids: list[int]) -> CustomerGroup:
        """Replace a group's entire membership with ``customer_ids``.

        Raises:
            CustomerGroupNotFoundError: No group exists with that id.
        """
        with self._session_scope() as session:
            group = CustomerGroupRepository(session).get_by_id_with_customers(group_id)
            if group is None:
                raise CustomerGroupNotFoundError(f"No customer group with id={group_id!r}.")
            customer_repo = CustomerRepository(session)
            members: list[Customer] = []
            for customer_id in customer_ids:
                customer = customer_repo.get_by_id(customer_id)
                if customer is not None:
                    members.append(customer)
            group.customers = members
            session.flush()
            return group

    def list_groups(self) -> list[CustomerGroup]:
        """List every group, ordered by name, with member customers loaded."""
        with self._session_scope() as session:
            return CustomerGroupRepository(session).list_all_with_customers()

    def get_group(self, group_id: int) -> CustomerGroup | None:
        """Fetch a single group with member customers loaded, or ``None`` if not found."""
        with self._session_scope() as session:
            return CustomerGroupRepository(session).get_by_id_with_customers(group_id)

"""Wires :class:`~developer_suite.models.customer.Customer` into the generic sync protocol.

The one module in this codebase allowed to know about both
:mod:`developer_suite.models.customer` and
:mod:`developer_suite.sync` — Phase 8's proof that a real business
entity plugs into the generic engine
(:mod:`developer_suite.sync.coordinator`) through a small, self
-contained module like this one, with zero changes required to the
generic layers underneath it. A future entity (employees, attendance,
departments, licenses, settings, ...) gets its own equally small
``*_sync.py`` module, not a change to this one or to
:mod:`developer_suite.sync.coordinator`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from developer_suite.models.customer import Customer, CustomerStatus
from developer_suite.repositories.customer_repository import CustomerRepository
from developer_suite.sync.client import PulledChange
from developer_suite.sync.coordinator import SyncCoordinator

ENTITY_TYPE = "customer"


def apply_customer_change(session: Session, change: PulledChange) -> None:
    """Upsert a local :class:`Customer` row from one pulled change.

    Writes through :class:`~developer_suite.repositories.customer_repository.CustomerRepository`
    directly, never through
    :class:`~developer_suite.services.customer_service.CustomerService`
    — going through the service would re-enqueue the very change being
    applied here into this installation's own outbox, looping its own
    pulled changes right back out forever.

    The pushed payload is always a full, self-describing snapshot of
    the entity (see
    :meth:`~developer_suite.services.customer_service.CustomerService._enqueue_sync`),
    including its own ``is_deleted``/``deleted_at`` — so this function
    does not need to branch on ``change.operation`` at all: applying
    any change, of any operation, reduces to "make the local row match
    this snapshot," which is naturally idempotent (re-applying this
    installation's own earlier push, seen again on a later pull, is a
    harmless no-op).

    Args:
        session: The open session for the current pull batch.
        change: The pulled change to apply.
    """
    repo = CustomerRepository(session)
    public_id = uuid.UUID(change.entity_id)
    customer = repo.get_by_public_id(public_id, include_deleted=True)

    if customer is None:
        customer = Customer(public_id=public_id)
        session.add(customer)

    payload = change.payload
    customer.company_name = payload["company_name"]
    customer.contact_name = payload["contact_name"]
    customer.phone = payload.get("phone")
    customer.email = payload.get("email")
    customer.address = payload.get("address")
    customer.notes = payload.get("notes")
    customer.status = CustomerStatus(payload["status"])
    customer.is_deleted = bool(payload.get("is_deleted", False))
    deleted_at_raw = payload.get("deleted_at")
    customer.deleted_at = datetime.fromisoformat(deleted_at_raw) if deleted_at_raw else None

    session.flush()


def register_customer_sync(coordinator: SyncCoordinator) -> None:
    """Register :func:`apply_customer_change` as the :data:`ENTITY_TYPE` applier.

    Args:
        coordinator: The :class:`~developer_suite.sync.coordinator.SyncCoordinator`
            to register against (see
            :class:`~developer_suite.container.ServiceContainer`).
    """
    coordinator.register_applier(ENTITY_TYPE, apply_customer_change)

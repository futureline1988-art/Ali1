"""Customer (vendor's customer registry) ORM model.

A row here represents one customer/company the vendor has sold the
Attendance Client to — the Developer Suite's own record *about* that
customer, never that customer's operational data (see this package's
``__init__.py``). A later phase links :class:`~models.license.License`-
equivalent records (not yet built — Phase 3 is Customer Management
only) to a ``customer_id`` foreign key here.
"""

from __future__ import annotations

from enum import Enum

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from developer_suite.database.base import DeveloperSuiteBaseModel
from models.base import enum_column_type


class CustomerStatus(str, Enum):
    """Whether a customer's account is currently active on the platform."""

    ACTIVE = "active"
    SUSPENDED = "suspended"


class Customer(DeveloperSuiteBaseModel):
    """A customer/company record in the vendor's own registry.

    Attributes:
        company_name: The customer's company/trade name.
        contact_name: The primary contact person at that company.
        phone: Contact phone number.
        email: Contact email address.
        address: Postal/street address.
        notes: Free-form vendor notes (support history, special
            arrangements, anything not worth its own column).
        status: Whether this customer's account is active or suspended
            (see :class:`CustomerStatus`).
    """

    company_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    contact_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[CustomerStatus] = mapped_column(
        enum_column_type(CustomerStatus),
        default=CustomerStatus.ACTIVE,
        nullable=False,
        index=True,
    )

    @property
    def is_active(self) -> bool:
        """Whether this customer's account is currently active."""
        return self.status is CustomerStatus.ACTIVE

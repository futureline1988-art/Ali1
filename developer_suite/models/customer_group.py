"""Customer group ORM model — a named, reusable set of customers.

Exists purely to make Phase 14's "target updates to a customer group"
selection convenient in the Update Manager UI; it carries no meaning
anywhere else in the platform (a customer's license, configuration
publishes, and everything else are entirely independent of group
membership). This table, like :class:`~developer_suite.models.customer.Customer`
itself, never leaves the Developer Suite's own database — the
Attendance Server has no concept of a customer or a customer group at
all (see :mod:`server.models.update`'s module docstring), so targeting
a group only ever means "resolve its members to their registered
device public ids, then send that flat list" — see
:mod:`developer_suite.services.update_manager_service`.
"""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from developer_suite.database.base import Base, DeveloperSuiteBaseModel
from developer_suite.models.customer import Customer

customer_group_members = Table(
    "customer_group_members",
    Base.metadata,
    Column("customer_id", ForeignKey("customers.id", ondelete="CASCADE"), primary_key=True),
    Column("customer_group_id", ForeignKey("customer_groups.id", ondelete="CASCADE"), primary_key=True),
)
"""The many-to-many association between customers and groups.

A plain association table, not its own
:class:`~developer_suite.database.base.DeveloperSuiteBaseModel` row:
membership itself has no attributes worth tracking (no joined-at
timestamp, no role) — it is exactly "is this customer in this group,"
nothing more.
"""


class CustomerGroup(DeveloperSuiteBaseModel):
    """A named, reusable set of customers.

    Attributes:
        name: The group's display name (e.g. ``"Enterprise
            customers"``); unique.
        customers: The customers currently in this group.
    """

    name: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)

    customers: Mapped[list["Customer"]] = relationship(
        "Customer", secondary=customer_group_members, backref="groups"
    )

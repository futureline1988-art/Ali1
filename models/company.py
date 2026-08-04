"""Company (tenant) ORM model — the root of the multi-company architecture.

Every tenant-owned table (branches, departments, employees, users,
roles, devices, attendance, ...) points back at a :class:`Company` via
:class:`~models.base.CompanyScopedMixin`. A ``Company`` itself is *not*
company-scoped — it is the tenant boundary that scoping is relative to.

Adding a new company therefore never requires a code change: it is a
single ``INSERT`` into this table (plus, typically, seeding a default
:class:`~models.role.Role` set), after which every existing repository
and service already filters by ``company_id`` and works for the new
tenant unmodified.

Licensing for the whole installation is handled separately, outside
this multi-tenant schema entirely — see :mod:`licensing` (the shared
machine-locked activation library both this application and the
vendor's Developer Suite use) rather than a per-``Company`` row here.
"""

from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import BaseModel


class Company(BaseModel):
    """A tenant company hosted by this installation.

    Attributes:
        name: Company's display/trade name; unique across the whole
            installation (companies are the top-level namespace, so this
            is the one identity field that legitimately stays globally
            unique rather than per-company).
        legal_name: Optional full legal/registered name, if different
            from the trade name.
        logo_path: Filesystem path to the company's logo image, used on
            the UI header, printed reports and employee badges.
        email: Company contact email.
        phone: Company contact phone number.
        address: Postal/street address.
        tax_number: Optional tax or commercial-registration number.
        is_active: Whether this tenant is currently allowed to operate.
            A deactivated company's users cannot log in (enforced by the
            service layer) regardless of any individual license's
            validity.
        branches: This company's :class:`~models.branch.Branch` records.
    """

    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(250), nullable=True)
    logo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    tax_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    branches: Mapped[list["Branch"]] = relationship(  # noqa: F821
        "Branch", back_populates="company"
    )

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return f"<Company id={self.id!r} name={self.name!r}>"

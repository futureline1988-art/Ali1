"""Company (tenant) ORM model — the root of the multi-company architecture.

Every tenant-owned table (branches, departments, employees, users,
roles, devices, attendance, ...) points back at a :class:`Company` via
:class:`~models.base.CompanyScopedMixin`. A ``Company`` itself is *not*
company-scoped — it is the tenant boundary that scoping is relative to.

Adding a new company therefore never requires a code change: it is a
single ``INSERT`` into this table (plus, typically, seeding a default
:class:`~models.role.Role` set and an initial
:class:`~models.license.License` — both service-layer concerns handled
in later files), after which every existing repository and service
already filters by ``company_id`` and works for the new tenant
unmodified.
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
        licenses: This company's :class:`~models.license.License`
            history (see that module for why more than one row may
            exist).
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
    licenses: Mapped[list["License"]] = relationship(  # noqa: F821
        "License", back_populates="company"
    )

    @property
    def current_license(self) -> "License | None":  # noqa: F821
        """The company's currently active license, if any.

        Returns the first :class:`~models.license.License` in
        :attr:`licenses` with ``is_active`` set; returns ``None`` if the
        company has no active license (e.g. expired trial not yet
        renewed).
        """
        for license_ in self.licenses:
            if license_.is_active:
                return license_
        return None

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return f"<Company id={self.id!r} name={self.name!r}>"

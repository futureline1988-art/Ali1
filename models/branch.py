"""Branch ORM model — a physical location belonging to a Company."""

from __future__ import annotations

from sqlalchemy import Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import BaseModel, CompanyScopedMixin


class Branch(CompanyScopedMixin, BaseModel):
    """A physical branch/location of a :class:`~models.company.Company`.

    Attributes:
        name: Branch name; unique within the owning company.
        code: Optional short code (e.g. ``"BAG-01"``); unique within the
            owning company when provided.
        address: Branch address.
        phone: Branch contact phone.
        is_main_branch: Whether this is the company's head office/main
            branch. Purely informational — it does not change how
            isolation or licensing limits are enforced.
        is_active: Whether the branch is currently operating.
        company: The owning :class:`~models.company.Company`.
    """

    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_branches_company_id_name"),
        UniqueConstraint("company_id", "code", name="uq_branches_company_id_code"),
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_main_branch: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    company: Mapped["Company"] = relationship("Company", back_populates="branches")  # noqa: F821

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return (
            f"<Branch id={self.id!r} name={self.name!r} "
            f"company_id={self.company_id!r}>"
        )

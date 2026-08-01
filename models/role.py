"""Role ORM model and the role/permission association.

Each company maintains its own, fully independent set of roles — even
the five built-in role kinds (:class:`~models.enums.UserRole`) are
materialized as separate ``Role`` rows per company, seeded when the
company is created. This is deliberate: if roles were shared globally,
editing one company's "HR" role permissions would silently change every
other company's "HR" role too, which would break the data-isolation
guarantee the multi-company architecture exists to provide. The
per-company row duplication is the price of real isolation.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, BaseModel, CompanyScopedMixin

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "permission_id",
        Integer,
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)
"""Many-to-many association between :class:`Role` and
:class:`~models.permission.Permission`. A plain association table (no
extra columns), so it is modeled as a :class:`sqlalchemy.Table` rather
than a mapped class."""


class Role(CompanyScopedMixin, BaseModel):
    """A named, permission-bearing role scoped to a single company.

    Attributes:
        name: Display name of the role (Arabic or English text, as the
            company entered it).
        code: Stable identifier matching a
            :class:`~models.enums.UserRole` value for the five built-in
            roles seeded at company creation; ``NULL`` for a custom role
            a company has defined on its own.
        description: Optional explanation of the role's purpose.
        is_system_role: Whether this is one of the built-in roles. The
            service layer uses this to block deletion or renaming of
            system roles while still allowing their permission set to be
            adjusted per company.
        permissions: The :class:`~models.permission.Permission` set
            granted to any :class:`~models.user.User` holding this role.
    """

    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_roles_company_id_name"),
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    code: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_system_role: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    permissions: Mapped[list["Permission"]] = relationship(  # noqa: F821
        "Permission", secondary=role_permissions, backref="roles"
    )

    def has_permission(self, code: str) -> bool:
        """Whether this role grants the permission identified by ``code``.

        Args:
            code: A :attr:`~models.permission.Permission.code` value.

        Returns:
            ``True`` if a granted permission with this code exists.
        """
        return any(permission.code == code for permission in self.permissions)

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return (
            f"<Role id={self.id!r} name={self.name!r} "
            f"company_id={self.company_id!r}>"
        )

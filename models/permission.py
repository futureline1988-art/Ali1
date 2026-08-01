"""Permission catalog ORM model — the fixed set of grantable capabilities."""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import BaseModel


class Permission(BaseModel):
    """A single grantable capability in the system's permission catalog.

    Permissions are global, not company-scoped: every company draws from
    the same fixed catalog of capabilities when assembling its own
    :class:`~models.role.Role` rows. The catalog is shipped and
    maintained with the software itself (seeded by a migration/service,
    not created by tenants), which is what lets new capabilities ship
    without any per-company migration.

    Attributes:
        code: Stable, dotted machine identifier (e.g.
            ``"employees.create"``, ``"reports.export"``), referenced
            from service-layer authorization checks.
        module: Grouping key for the owning module (e.g.
            ``"employees"``, ``"attendance"``, ``"devices"``), used to
            organize the permissions-management UI.
        name_ar: Arabic display name.
        name_en: English display name.
        description: Optional longer explanation of what the permission
            allows.
    """

    code: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    module: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name_ar: Mapped[str] = mapped_column(String(150), nullable=False)
    name_en: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return f"<Permission id={self.id!r} code={self.code!r}>"

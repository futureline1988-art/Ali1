"""Department ORM model.

Departments organize employees for reporting, filtering and (optionally)
a hierarchy of sub-departments. Kept intentionally decoupled from
:class:`~models.employee.Employee` — this module has no knowledge of
employees; :class:`~models.employee.Employee` is the side that points at
a department, not the other way around, which avoids any circular
import between the two model modules.
"""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import BaseModel


class Department(BaseModel):
    """An organizational department, optionally nested under a parent.

    Attributes:
        name: Department name (Arabic or English text, stored as-is);
            unique across the company.
        code: Optional short unique code (e.g. ``"IT"``, ``"HR"``) used in
            employee numbering schemes and compact report columns.
        description: Optional free-form description.
        is_active: Whether the department is currently in use. Distinct
            from :attr:`~models.base.SoftDeleteMixin.is_deleted` — an
            inactive department is hidden from new-assignment pickers but
            still a valid historical record.
        parent_department_id: Optional self-referential foreign key
            forming a department hierarchy (e.g. "IT" under "Operations").
            When the parent is deleted, children are detached
            (``parent_department_id`` set to ``NULL``), never
            cascade-deleted.
        parent: The parent :class:`Department`, if any.
        children: Sub-departments whose :attr:`parent_department_id`
            points at this department.
    """

    __table_args__ = (
        CheckConstraint(
            "parent_department_id IS NULL OR parent_department_id != id",
            name="ck_departments_no_self_parent",
        ),
    )

    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    code: Mapped[str | None] = mapped_column(
        String(20), unique=True, index=True, nullable=True
    )
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    parent_department_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    parent: Mapped["Department | None"] = relationship(
        remote_side="Department.id", back_populates="children"
    )
    children: Mapped[list["Department"]] = relationship(
        back_populates="parent"
    )

    @property
    def full_path(self) -> str:
        """Human-readable hierarchy path, e.g. ``"الإدارة العامة / تقنية المعلومات"``.

        Walks up :attr:`parent` links to the root. Callers (typically the
        service layer, when re-parenting a department) are responsible
        for never introducing a cycle in the hierarchy; this property
        does not itself guard against one.
        """
        names = [self.name]
        node = self.parent
        while node is not None:
            names.append(node.name)
            node = node.parent
        return " / ".join(reversed(names))

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return f"<Department id={self.id!r} name={self.name!r}>"

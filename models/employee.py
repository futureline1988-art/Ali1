"""Employee ORM model.

Represents a person whose attendance is tracked by the system. Distinct
from :class:`~models.user.User` (a login account for the software
itself) — an employee need not ever log in, and a user need not be an
employee.

Photo, QR code and barcode are stored as filesystem paths (under
``config.PathsConfig.uploads_dir``) rather than as database BLOBs, which
keeps the database small and portable and lets the UI display images
directly from disk.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Date,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import BaseModel, CompanyScopedMixin, enum_column_type
from models.encrypted_types import EncryptedDecimal
from models.enums import EmploymentStatus


class Employee(CompanyScopedMixin, BaseModel):
    """A person whose attendance and HR data is tracked by the system.

    Attributes:
        company_id: The owning company (see
            :class:`~models.base.CompanyScopedMixin`).
        branch_id: The branch this employee physically works at, if the
            company tracks that. The service layer is responsible for
            rejecting a branch from a different company.
        employee_number: Business identifier printed on badges and used
            as the QR/barcode payload; assigned by HR, not the database
            (so numbering schemes like ``"EMP-0042"`` are possible).
            Unique within the owning company.
        full_name: Employee's full name (Arabic or English text).
        national_id: Optional national identification number; unique
            within the owning company when provided. Not enforced
            globally — different tenant companies are isolated from each
            other and must never collide on, or leak information about,
            each other's data.
        department_id: The employee's department, if assigned.
        position: Free-form job title (e.g. ``"محاسب"``, ``"Accountant"``).
        salary: Base salary; ``NULL`` if not yet set. Encrypted at rest
            by :class:`~models.encrypted_types.EncryptedDecimal`
            (transparent to every caller - reading/assigning this
            attribute always sees a real ``Decimal``, never a binary
            float, so exact decimal arithmetic is preserved even though
            the underlying column is now opaque ciphertext text rather
            than a native numeric type). Because of that, the
            non-negative check this column used to enforce at the
            database level (``salary >= 0``) can no longer be — a SQL
            comparison against ciphertext is meaningless — so it is
            enforced exclusively in the service layer now (see
            ``services/employee_service.py``'s use of
            :func:`~utils.validators.is_valid_salary`, which already
            ran before any database write reached this constraint).
        phone: Contact phone number.
        email: Contact email address; unique within the owning company
            when provided.
        notes: Free-form HR notes.
        employment_status: Current :class:`~models.enums.EmploymentStatus`.
        hire_date: Date the employee joined; used by leave-accrual and
            seniority calculations in the service layer.
        photo_path: Filesystem path to the employee's personal photo.
        qr_code_path: Filesystem path to a generated QR code encoding
            this employee's identity (see ``utils/qr_barcode.py``).
        barcode_path: Filesystem path to a generated barcode.
        department: The related :class:`~models.department.Department`.
    """

    __table_args__ = (
        UniqueConstraint(
            "company_id", "employee_number", name="uq_employees_company_id_employee_number"
        ),
        UniqueConstraint(
            "company_id", "national_id", name="uq_employees_company_id_national_id"
        ),
        UniqueConstraint("company_id", "email", name="uq_employees_company_id_email"),
    )

    branch_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    employee_number: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    national_id: Mapped[str | None] = mapped_column(String(50), index=True, nullable=True)

    department_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    position: Mapped[str | None] = mapped_column(String(100), nullable=True)
    salary: Mapped[Decimal | None] = mapped_column(EncryptedDecimal, nullable=True)

    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    employment_status: Mapped[EmploymentStatus] = mapped_column(
        enum_column_type(EmploymentStatus),
        nullable=False,
        default=EmploymentStatus.ACTIVE,
        index=True,
    )
    hire_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    photo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    qr_code_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    barcode_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    department: Mapped["Department | None"] = relationship(  # noqa: F821 - resolved via registry
        "Department", backref="employees"
    )

    @property
    def is_currently_active(self) -> bool:
        """Whether this employee should be treated as actively working.

        Combines soft-delete state with :attr:`employment_status` so
        callers do not need to check both independently.
        """
        return not self.is_deleted and self.employment_status is EmploymentStatus.ACTIVE

    @property
    def employment_status_label_ar(self) -> str:
        """Arabic display label for :attr:`employment_status`."""
        return self.employment_status.label_ar

    @property
    def employment_status_label_en(self) -> str:
        """English display label for :attr:`employment_status`."""
        return self.employment_status.label_en

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return (
            f"<Employee id={self.id!r} employee_number={self.employee_number!r} "
            f"full_name={self.full_name!r}>"
        )

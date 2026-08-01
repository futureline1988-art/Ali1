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

from sqlalchemy import CheckConstraint, Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import BaseModel, enum_column_type
from models.enums import EmploymentStatus


class Employee(BaseModel):
    """A person whose attendance and HR data is tracked by the system.

    Attributes:
        employee_number: Unique business identifier printed on badges and
            used as the QR/barcode payload; assigned by HR, not the
            database (so numbering schemes like ``"EMP-0042"`` are
            possible).
        full_name: Employee's full name (Arabic or English text).
        national_id: Optional national identification number; unique
            when provided.
        department_id: The employee's department, if assigned.
        position: Free-form job title (e.g. ``"محاسب"``, ``"Accountant"``).
        salary: Base salary; ``NULL`` if not yet set. Stored as
            ``Numeric(12, 2)`` for exact decimal arithmetic — payroll
            figures must never be represented as binary floats.
        phone: Contact phone number.
        email: Contact email address; unique when provided.
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
        CheckConstraint(
            "salary IS NULL OR salary >= 0", name="ck_employees_salary_non_negative"
        ),
    )

    employee_number: Mapped[str] = mapped_column(
        String(30), unique=True, index=True, nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    national_id: Mapped[str | None] = mapped_column(
        String(50), unique=True, index=True, nullable=True
    )

    department_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    position: Mapped[str | None] = mapped_column(String(100), nullable=True)
    salary: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )
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

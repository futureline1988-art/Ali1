"""Print profile ORM model — a reusable report/receipt layout template."""

from __future__ import annotations

from enum import Enum

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from developer_suite.database.base import DeveloperSuiteBaseModel
from models.base import enum_column_type


class PaperSize(str, Enum):
    """Supported print paper sizes."""

    A4 = "a4"
    A5 = "a5"
    THERMAL_80MM = "thermal_80mm"


class PrintProfile(DeveloperSuiteBaseModel):
    """A reusable report/receipt layout template.

    Attributes:
        name: A unique, human-readable identifier for this profile.
        paper_size: Which :class:`PaperSize` this profile lays out for.
        header_text: Optional text printed above generated documents.
        footer_text: Optional text printed below generated documents.
        show_company_logo: Whether the company logo is included.
        show_qr_code: Whether a QR code is included (see
            :mod:`utils.qr_barcode`, unrelated — this only records the
            *preference*, not how one is generated).
        margin_mm: Page margin, in millimeters.
    """

    name: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    paper_size: Mapped[PaperSize] = mapped_column(
        enum_column_type(PaperSize), default=PaperSize.A4, nullable=False
    )
    header_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    footer_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    show_company_logo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    show_qr_code: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    margin_mm: Mapped[int] = mapped_column(Integer, default=15, nullable=False)

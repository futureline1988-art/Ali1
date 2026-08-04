"""Theme profile ORM model — a reusable branding/appearance template.

A named bundle of colors, mode, font, and (Phase 13) language choices
that a :class:`~developer_suite.models.remote_configuration.RemoteConfiguration`
references. Phase 5 only stored and edited these templates; Phase 13
adds :attr:`ThemeProfile.language` and is the first phase that
actually publishes a bundle to a customer's Attendance Client (see
:mod:`developer_suite.sync.configuration_sync`) — language rides
along on this profile rather than getting its own, since it is the
same kind of "how the UI presents itself" choice as
:attr:`ThemeProfile.mode`/:attr:`ThemeProfile.font_family`, and
:class:`~models.company_settings.CompanySettings` (the Attendance
Client's own existing per-company settings row this profile is
ultimately applied onto) already stores language and theme as two
fields on the same row.
"""

from __future__ import annotations

from enum import Enum

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from developer_suite.database.base import DeveloperSuiteBaseModel
from models.base import enum_column_type


class ThemeMode(str, Enum):
    """Light/dark UI mode a theme profile targets."""

    LIGHT = "light"
    DARK = "dark"


class ThemeProfile(DeveloperSuiteBaseModel):
    """A reusable branding/appearance template.

    Attributes:
        name: A unique, human-readable identifier for this profile
            (e.g. ``"الوضع الافتراضي"``, ``"شعار عيد الفطر"``).
        mode: Whether this profile targets light or dark mode.
        primary_color: Hex color (e.g. ``"#1976D2"``) for the primary
            UI accent.
        secondary_color: Hex color for the secondary UI accent.
        accent_color: Optional hex color for a tertiary highlight
            color.
        logo_path: Optional filesystem path to a logo asset.
        font_family: The UI font family, matching
            :attr:`config.UIConfig.default_font_family_ar`'s naming
            convention (default ``"Cairo"``, this application's own
            existing default).
        language: Default UI language, ``"ar"`` or ``"en"`` — same
            two-letter convention and default as
            :attr:`~models.company_settings.CompanySettings.language`.
    """

    name: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    mode: Mapped[ThemeMode] = mapped_column(
        enum_column_type(ThemeMode), default=ThemeMode.LIGHT, nullable=False
    )
    primary_color: Mapped[str] = mapped_column(String(9), default="#1976D2", nullable=False)
    secondary_color: Mapped[str] = mapped_column(String(9), default="#424242", nullable=False)
    accent_color: Mapped[str | None] = mapped_column(String(9), nullable=True)
    logo_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    font_family: Mapped[str] = mapped_column(String(100), default="Cairo", nullable=False)
    language: Mapped[str] = mapped_column(String(2), default="ar", nullable=False)

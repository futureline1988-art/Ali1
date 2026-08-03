"""Theme profile ORM model — a reusable branding/appearance template.

A named bundle of colors, mode, and font choices that a
:class:`~developer_suite.models.remote_configuration.RemoteConfiguration`
references. Storing and editing these templates is this phase's entire
scope: nothing here is pushed anywhere yet (see this package's
``remote_configuration.py`` docstring).
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

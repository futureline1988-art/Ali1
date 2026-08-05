"""Per-company settings ORM model.

A one-row-per-company configuration table — enforced as a true 1:1
relationship via a ``UNIQUE`` constraint on ``company_id`` — covering
everything a tenant can customize about how the software behaves for
them: language, theme, date/currency formatting, the display timezone
used to render the UTC timestamps every other table stores, automatic
backup policy, and biometric device connection defaults.

Kept deliberately decoupled from ``config.py``: this table stores
per-company *overrides* of the application's global defaults, not a
duplicate of the config module's enums, so no model in this package
needs to import from ``config``.

Also holds this company's visual branding — primary/secondary/accent
colors and UI font — set once by :mod:`ui.first_run_wizard` and
editable afterward from the Settings screen; every branding column is
nullable so a row from before branding existed simply has them unset.
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, CheckConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from models.base import BaseModel, CompanyScopedMixin


class CompanySettings(CompanyScopedMixin, BaseModel):
    """A company's configurable preferences, one row per company.

    Attributes:
        language: Default UI language for this company's users, either
            ``"ar"`` or ``"en"``.
        theme: Default UI theme, either ``"light"`` or ``"dark"``.
        date_format: ``strftime``-style date format used across the UI
            and generated reports (e.g. ``"%d/%m/%Y"``).
        time_format_24h: Whether times are displayed in 24-hour format.
        currency_code: ISO-4217-style currency code (e.g. ``"IQD"``).
        currency_symbol: Currency symbol used in reports (e.g.
            ``"د.ع"``).
        timezone_name: IANA timezone name (e.g. ``"Asia/Baghdad"``) used
            to render the UTC timestamps stored everywhere else in the
            schema as this company's local wall-clock time.
        auto_backup_enabled: Whether scheduled automatic backups are on.
        backup_interval_hours: Hours between automatic backups.
        backup_retention_count: Number of past backups to keep before
            the oldest is pruned.
        default_device_timeout_seconds: Fallback connection timeout for
            this company's devices when a device does not specify its
            own override.
        default_sync_interval_minutes: Fallback interval between
            automatic device sync runs.
        theme_primary_color: Hex primary brand color, set by the
            first-run wizard; ``None`` until then.
        theme_secondary_color: Hex secondary brand color.
        theme_accent_color: Optional hex tertiary highlight color.
        theme_font_family: The UI font family.
        print_settings: Print/report layout overrides (paper size,
            header/footer text, logo/QR toggles, margin) as a JSON
            dict — kept as one JSON column rather than five flat ones
            since nothing in this application queries, sorts, or
            filters by an individual print setting.
        attendance_policy_settings: Attendance/shift/overtime rule
            overrides (grace periods, overtime threshold, half-day
            threshold, working days) as a JSON dict, for the same
            reason.
        company: The related :class:`~models.company.Company`.
    """

    # TableNameMixin's generic pluralization would turn this into
    # "company_settingses" (it treats the trailing "s" in "Settings" as
    # needing "+es", same rule that correctly turns "bus" into "buses").
    # "Settings" is already plural, so the table name is overridden
    # explicitly here rather than adjusting the shared heuristic.
    __tablename__ = "company_settings"

    __table_args__ = (
        UniqueConstraint("company_id", name="uq_company_settings_company_id"),
        CheckConstraint("language IN ('ar', 'en')", name="ck_company_settings_language"),
        CheckConstraint("theme IN ('light', 'dark')", name="ck_company_settings_theme"),
    )

    language: Mapped[str] = mapped_column(
        String(2), default="ar", server_default="ar", nullable=False
    )
    theme: Mapped[str] = mapped_column(
        String(5), default="light", server_default="light", nullable=False
    )
    date_format: Mapped[str] = mapped_column(
        String(20), default="%d/%m/%Y", server_default="%d/%m/%Y", nullable=False
    )
    time_format_24h: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    currency_code: Mapped[str] = mapped_column(
        String(3), default="IQD", server_default="IQD", nullable=False
    )
    currency_symbol: Mapped[str] = mapped_column(
        String(10), default="د.ع", nullable=False
    )
    timezone_name: Mapped[str] = mapped_column(
        String(50), default="Asia/Baghdad", server_default="Asia/Baghdad", nullable=False
    )

    auto_backup_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    backup_interval_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    backup_retention_count: Mapped[int] = mapped_column(Integer, default=14, nullable=False)

    default_device_timeout_seconds: Mapped[int] = mapped_column(
        Integer, default=8, nullable=False
    )
    default_sync_interval_minutes: Mapped[int] = mapped_column(
        Integer, default=15, nullable=False
    )

    theme_primary_color: Mapped[str | None] = mapped_column(String(9), nullable=True)
    theme_secondary_color: Mapped[str | None] = mapped_column(String(9), nullable=True)
    theme_accent_color: Mapped[str | None] = mapped_column(String(9), nullable=True)
    theme_font_family: Mapped[str | None] = mapped_column(String(100), nullable=True)
    print_settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    attendance_policy_settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    company: Mapped["Company"] = relationship(  # noqa: F821
        "Company", backref=backref("settings", uselist=False)
    )

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return (
            f"<CompanySettings id={self.id!r} company_id={self.company_id!r} "
            f"language={self.language!r} theme={self.theme!r}>"
        )

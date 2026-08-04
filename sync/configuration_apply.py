"""Applies a pulled ``"company_configuration"`` change to local tables.

The pull-side counterpart to
:mod:`developer_suite.sync.configuration_sync`'s ``build_payload`` —
this module writes that same nested payload shape back into
:class:`~models.company.Company` and
:class:`~models.company_settings.CompanySettings`, the two existing
models Phase 13 designated as the landing spot for remote
configuration (see :mod:`models.company_settings`'s own module
docstring for why the branding/print/policy columns live there instead
of new tables).

Registered as this installation's one
:mod:`sync.coordinator`-style applier for ``ENTITY_TYPE`` — never
called directly by UI code, only by
:func:`~sync.coordinator.ClientSyncCoordinator.pull_and_apply`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from repositories.company_repository import CompanyRepository
from repositories.company_settings_repository import CompanySettingsRepository
from sync.client import PulledChange

ENTITY_TYPE = "company_configuration"


class NoActiveCompanyError(Exception):
    """Raised when a configuration change is pulled but no active company exists to apply it to.

    This installation applies every published configuration to the
    first active :class:`~models.company.Company` row (see this
    module's docstring) — a pragmatic single-tenant-per-installation
    scoping decision, since nothing in the desktop application
    currently disambiguates "which of this installation's companies a
    remote publish targets" any further.
    """


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ApplyResult:
    """The outcome of applying one pulled configuration change.

    Attributes:
        applied: Whether the payload was actually written (``False``
            when its checksum matched the already-applied
            configuration — see :mod:`sync.configuration_apply`'s
            "never reapply if nothing changed" contract).
        restart_required: Whether this change altered a setting
            (language or UI font) that only takes full effect after
            the application restarts.
    """

    applied: bool
    restart_required: bool


def apply_configuration_change(session: Session, change: PulledChange) -> ApplyResult:
    """Write one pulled configuration payload into ``Company``/``CompanySettings``.

    Args:
        session: The open session for the current pull batch.
        change: The pulled change; ``change.payload`` must be shaped
            exactly like
            :func:`~developer_suite.sync.configuration_sync.build_payload`'s
            return value.

    Returns:
        Whether anything was actually applied, and whether a restart
        is now required.

    Raises:
        NoActiveCompanyError: No active company exists locally to
            apply the configuration to.
    """
    companies = CompanyRepository(session).list_active()
    if not companies:
        raise NoActiveCompanyError(
            "Cannot apply a pulled configuration change: no active company exists locally."
        )
    company = companies[0]
    settings = CompanySettingsRepository(session, company_id=company.id).get_or_create()

    payload = change.payload
    checksum = _payload_checksum(change)
    if checksum is not None and settings.remote_config_checksum == checksum:
        return ApplyResult(applied=False, restart_required=False)

    restart_required = _apply_company_group(company, payload.get("company") or {})
    restart_required = _apply_theme_group(settings, payload.get("theme") or {}, company) or restart_required
    _apply_print_group(settings, payload.get("print"))
    _apply_attendance_policy_group(settings, payload.get("attendance_policy"))
    _apply_device_group(settings, payload.get("device"))
    _apply_backup_group(settings, payload.get("backup"))

    settings.remote_config_version = change.new_version
    settings.remote_config_checksum = checksum
    settings.remote_config_applied_at = _utc_now()
    if restart_required:
        settings.remote_config_restart_required = True

    session.flush()
    return ApplyResult(applied=True, restart_required=restart_required)


def _payload_checksum(change: PulledChange) -> str | None:
    """Recompute the payload's checksum for the "already applied" comparison.

    Recomputed locally (rather than trusting a checksum field on the
    wire) so this comparison is correct even if a future change to the
    pull response ever omits one — see :func:`sync.protocol.compute_checksum`.
    """
    from sync.protocol import compute_checksum

    return compute_checksum(change.payload)


def _apply_company_group(company, group: dict) -> bool:
    """Apply the ``"company"`` payload group; never affects :attr:`ApplyResult.restart_required`."""
    if group.get("name"):
        company.name = group["name"]
    if "phone" in group:
        company.phone = group["phone"]
    if "email" in group:
        company.email = group["email"]
    if "address" in group:
        company.address = group["address"]
    return False


def _apply_theme_group(settings, group: dict, company) -> bool:
    """Apply the ``"theme"`` payload group; returns whether a restart is now required."""
    restart_required = False
    if "language" in group and group["language"] != settings.language:
        restart_required = True
    if "language" in group:
        settings.language = group["language"]
    if "mode" in group:
        settings.theme = group["mode"]
    if "primary_color" in group:
        settings.theme_primary_color = group["primary_color"]
    if "secondary_color" in group:
        settings.theme_secondary_color = group["secondary_color"]
    if "accent_color" in group:
        settings.theme_accent_color = group["accent_color"]
    if "font_family" in group and group["font_family"] != settings.theme_font_family:
        restart_required = True
    if "font_family" in group:
        settings.theme_font_family = group["font_family"]
    if group.get("logo_path"):
        company.logo_path = group["logo_path"]
    return restart_required


def _apply_print_group(settings, group: dict | None) -> None:
    """Apply the ``"print"`` payload group verbatim as one JSON column."""
    if group is not None:
        settings.print_settings = group


def _apply_attendance_policy_group(settings, group: dict | None) -> None:
    """Apply the ``"attendance_policy"`` payload group verbatim as one JSON column."""
    if group is not None:
        settings.attendance_policy_settings = group


def _apply_device_group(settings, group: dict | None) -> None:
    """Apply the subset of the ``"device"`` payload group with a local column.

    Only ``timeout_seconds``/``sync_interval_minutes`` land on
    :class:`~models.company_settings.CompanySettings`; ``protocol``,
    ``default_port``, and ``auto_reconnect`` have no existing local
    column to apply to and are intentionally not stored, per "reuse
    existing models, do not duplicate" — see this module's docstring.
    """
    if group is None:
        return
    if "timeout_seconds" in group:
        settings.default_device_timeout_seconds = group["timeout_seconds"]
    if "sync_interval_minutes" in group:
        settings.default_sync_interval_minutes = group["sync_interval_minutes"]


def _apply_backup_group(settings, group: dict | None) -> None:
    """Apply the subset of the ``"backup"`` payload group with a local column."""
    if group is None:
        return
    if "enabled" in group:
        settings.auto_backup_enabled = group["enabled"]
    if "interval_hours" in group:
        settings.backup_interval_hours = group["interval_hours"]
    if "retention_count" in group:
        settings.backup_retention_count = group["retention_count"]

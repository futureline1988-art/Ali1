"""Wires configuration publishing into the generic sync protocol.

Deliberately one-directional, unlike
:mod:`developer_suite.sync.customer_sync`: a configuration publish only
ever flows Developer Suite → Attendance Server → Attendance Client, so
this module registers nothing with
:meth:`~developer_suite.sync.coordinator.SyncCoordinator.register_applier`
— there is nothing for *this* installation to pull and apply back
(:mod:`developer_suite.sync.coordinator`'s pull loop pulls every
entity type it has a registered applier for; simply never registering
one for :data:`ENTITY_TYPE` here means Developer Suite pull cycles skip
it entirely, with no special-casing needed anywhere in the generic
layer).

:func:`build_payload` is the single source of truth for what "a
configuration" actually contains on the wire — both
:class:`~developer_suite.services.configuration_publish_service.ConfigurationPublishService`
(to compute what to push) and any test asserting on payload shape
import it, so the shape is defined exactly once.
"""

from __future__ import annotations

from developer_suite.models.customer import Customer
from developer_suite.models.remote_configuration import RemoteConfiguration
from developer_suite.sync.protocol import compute_checksum

ENTITY_TYPE = "company_configuration"


def build_payload(customer: Customer, remote_configuration: RemoteConfiguration) -> dict:
    """Build the complete, self-contained configuration snapshot to publish.

    Every requested Phase 13 sync item is covered by one of these two
    already-existing sources, reused verbatim (see
    ``developer_suite/models/remote_configuration.py``'s and
    ``developer_suite/models/customer.py``'s own docstrings): company
    information from ``customer``, everything else from
    ``remote_configuration``'s five bundled profiles.

    Args:
        customer: Whose company-information fields to snapshot.
        remote_configuration: Whose five bundled profiles to snapshot
            (theme — including logo/colors/font/language, print,
            attendance policy — grace periods/overtime/working-days
            "holiday" defaults, device, and backup settings).

    Returns:
        A JSON-safe dict, stable field order not guaranteed but every
        key always present (``None`` rather than omitted, so a
        receiver can tell "explicitly cleared" from "this version's
        schema didn't have this field yet").
    """
    theme = remote_configuration.theme_profile
    print_profile = remote_configuration.print_profile
    attendance_policy = remote_configuration.attendance_policy_profile
    device = remote_configuration.device_profile
    backup = remote_configuration.backup_profile

    return {
        "company": {
            "name": customer.company_name,
            "contact_name": customer.contact_name,
            "phone": customer.phone,
            "email": customer.email,
            "address": customer.address,
        },
        "theme": {
            "mode": theme.mode.value,
            "primary_color": theme.primary_color,
            "secondary_color": theme.secondary_color,
            "accent_color": theme.accent_color,
            "logo_path": theme.logo_path,
            "font_family": theme.font_family,
            "language": theme.language,
        },
        "print": {
            "paper_size": print_profile.paper_size.value,
            "header_text": print_profile.header_text,
            "footer_text": print_profile.footer_text,
            "show_company_logo": print_profile.show_company_logo,
            "show_qr_code": print_profile.show_qr_code,
            "margin_mm": print_profile.margin_mm,
        },
        "attendance_policy": {
            "grace_period_minutes": attendance_policy.grace_period_minutes,
            "early_leave_grace_minutes": attendance_policy.early_leave_grace_minutes,
            "overtime_threshold_minutes": attendance_policy.overtime_threshold_minutes,
            "half_day_threshold_hours": attendance_policy.half_day_threshold_hours,
            "working_days": list(attendance_policy.working_days),
        },
        "device": {
            "protocol": device.protocol.value,
            "default_port": device.default_port,
            "timeout_seconds": device.timeout_seconds,
            "sync_interval_minutes": device.sync_interval_minutes,
            "auto_reconnect": device.auto_reconnect,
        },
        "backup": {
            "enabled": backup.enabled,
            "interval_hours": backup.interval_hours,
            "retention_count": backup.retention_count,
            "location_type": backup.location_type.value,
            "encrypt_backups": backup.encrypt_backups,
        },
    }


def compute_payload_checksum(payload: dict) -> str:
    """Checksum a configuration payload, using the shared protocol algorithm."""
    return compute_checksum(payload)

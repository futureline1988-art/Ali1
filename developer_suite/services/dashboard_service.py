"""Aggregates existing services' data into one dashboard snapshot.

No business logic lives here that does not already exist elsewhere:
every count is a plain aggregation over
:meth:`~developer_suite.services.customer_service.CustomerService.search_customers`/
:meth:`~developer_suite.services.license_service.LicenseService.search_licenses`
results (using those models' own
:attr:`~developer_suite.models.license.IssuedLicense.is_active`/
:attr:`~developer_suite.models.license.IssuedLicense.is_expired`/
:attr:`~developer_suite.models.license.IssuedLicense.days_remaining`
properties rather than recomputing "is this license active" here),
:class:`~developer_suite.sync.status.SyncStatus` is read verbatim from
:class:`~developer_suite.sync.scheduler.SyncSchedulerService`, and
remote server/device data is read verbatim from
:class:`~developer_suite.admin.client.AdminApiClient`. This module's
only job is presentation-shaping: turning several services' outputs
into one :class:`DashboardSnapshot` a UI page can render without
itself importing five different services.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from developer_suite.admin.client import AdminApiClient, AdminApiError
from developer_suite.config import DeveloperSuiteConfig
from developer_suite.models.customer import CustomerStatus
from developer_suite.models.license import IssuedLicense
from developer_suite.services.customer_service import CustomerService
from developer_suite.services.license_service import LicenseService
from developer_suite.sync.scheduler import SyncSchedulerService
from licensing.enums import LicenseType

_DEFAULT_UPCOMING_EXPIRATION_WINDOW_DAYS = 30


@dataclass(frozen=True)
class UpcomingExpiration:
    """One active license expiring soon, for the dashboard's expiration list."""

    customer_name: str
    license_type_label: str
    expires_at: date
    days_remaining: int


@dataclass(frozen=True)
class DashboardSnapshot:
    """Everything the main Developer Dashboard displays, computed once per refresh.

    Attributes:
        total_customers: Every non-deleted customer.
        active_customers: Customers whose :attr:`~developer_suite.models.customer.Customer.status`
            is :attr:`~developer_suite.models.customer.CustomerStatus.ACTIVE`.
        suspended_customers: The rest.
        online_companies: Registered Attendance Client installations
            currently considered online (see
            :meth:`~developer_suite.admin.client.DeviceInfo.is_online`);
            ``None`` if this could not be determined (no admin token
            configured, or the server is unreachable).
        offline_companies: The rest of the registered Attendance Client
            installations; ``None`` under the same conditions.
        active_licenses: Licenses currently in good standing (see
            :attr:`~developer_suite.models.license.IssuedLicense.is_active`).
        expired_licenses: Licenses past their expiration date.
        trial_licenses: Licenses of
            :attr:`~licensing.enums.LicenseType.TRIAL`.
        upcoming_expirations: Active licenses expiring within the
            configured window, soonest first.
        last_sync_at: This installation's last successful synchronization.
        pending_sync_count: Local changes still queued to push.
        server_reachable: Whether ``/health`` answered; ``None`` if
            not yet checked.
        server_version: The Attendance Server's own reported version,
            if reachable.
        platform_version: This Developer Suite installation's own
            version.
    """

    total_customers: int = 0
    active_customers: int = 0
    suspended_customers: int = 0
    online_companies: int | None = None
    offline_companies: int | None = None
    active_licenses: int = 0
    expired_licenses: int = 0
    trial_licenses: int = 0
    upcoming_expirations: list[UpcomingExpiration] = field(default_factory=list)
    last_sync_at: datetime | None = None
    pending_sync_count: int = 0
    server_reachable: bool | None = None
    server_version: str | None = None
    platform_version: str = ""


class DashboardService:
    """Builds one :class:`DashboardSnapshot` per call, from already-existing services."""

    def __init__(
        self,
        customer_service: CustomerService,
        license_service: LicenseService,
        sync_scheduler: SyncSchedulerService,
        admin_client: AdminApiClient,
        config: DeveloperSuiteConfig,
        *,
        upcoming_expiration_window_days: int = _DEFAULT_UPCOMING_EXPIRATION_WINDOW_DAYS,
    ) -> None:
        """Create a dashboard service over already-constructed dependencies.

        Args:
            customer_service: Source of customer counts.
            license_service: Source of license counts and expirations.
            sync_scheduler: Source of this installation's own sync status.
            admin_client: Source of remote device/server data — every
                call is wrapped so an unreachable server or a missing
                admin token degrades those specific fields to ``None``
                rather than failing the whole snapshot.
            config: Supplies :attr:`~developer_suite.config.DeveloperSuiteConfig.app_version`.
            upcoming_expiration_window_days: How many days ahead counts
                as "upcoming" for :attr:`DashboardSnapshot.upcoming_expirations`.
        """
        self._customer_service = customer_service
        self._license_service = license_service
        self._sync_scheduler = sync_scheduler
        self._admin_client = admin_client
        self._config = config
        self._upcoming_window_days = upcoming_expiration_window_days

    def get_snapshot(self) -> DashboardSnapshot:
        """Compute a fresh dashboard snapshot.

        Never raises for a remote-data failure — see
        :attr:`DashboardSnapshot.online_companies`/:attr:`DashboardSnapshot.server_reachable`'s
        own docstrings for how that shows up instead.
        """
        customers = self._customer_service.search_customers("")
        total_customers = len(customers)
        active_customers = sum(1 for customer in customers if customer.status is CustomerStatus.ACTIVE)

        licenses = self._license_service.search_licenses("")
        active_licenses = sum(1 for license_record in licenses if license_record.is_active)
        expired_licenses = sum(1 for license_record in licenses if license_record.is_expired)
        trial_licenses = sum(1 for license_record in licenses if license_record.license_type is LicenseType.TRIAL)
        upcoming_expirations = _upcoming_expirations(licenses, window_days=self._upcoming_window_days)

        sync_status = self._sync_scheduler.get_status()

        online_companies, offline_companies = self._count_companies_by_connectivity()
        server_reachable, server_version = self._probe_server()

        return DashboardSnapshot(
            total_customers=total_customers,
            active_customers=active_customers,
            suspended_customers=total_customers - active_customers,
            online_companies=online_companies,
            offline_companies=offline_companies,
            active_licenses=active_licenses,
            expired_licenses=expired_licenses,
            trial_licenses=trial_licenses,
            upcoming_expirations=upcoming_expirations,
            last_sync_at=sync_status.last_success_at,
            pending_sync_count=sync_status.pending_changes_count,
            server_reachable=server_reachable,
            server_version=server_version,
            platform_version=self._config.app_version,
        )

    def _count_companies_by_connectivity(self) -> tuple[int | None, int | None]:
        try:
            devices = self._admin_client.list_devices()
        except AdminApiError:
            return None, None
        client_devices = [device for device in devices if device.device_type == "attendance_client"]
        online = sum(1 for device in client_devices if device.is_online())
        return online, len(client_devices) - online

    def _probe_server(self) -> tuple[bool, str | None]:
        reachable = self._admin_client.check_health()
        if not reachable:
            return False, None
        version_info = self._admin_client.get_version()
        return True, version_info["app_version"] if version_info else None


def _upcoming_expirations(
    licenses: list[IssuedLicense], *, window_days: int
) -> list[UpcomingExpiration]:
    """Active licenses expiring within ``window_days``, soonest first."""
    entries = [
        UpcomingExpiration(
            customer_name=license_record.customer.company_name,
            license_type_label=license_record.license_type.label_ar,
            expires_at=license_record.expires_at,
            days_remaining=license_record.days_remaining,
        )
        for license_record in licenses
        if license_record.is_active
        and license_record.days_remaining is not None
        and 0 <= license_record.days_remaining <= window_days
    ]
    entries.sort(key=lambda entry: entry.days_remaining)
    return entries

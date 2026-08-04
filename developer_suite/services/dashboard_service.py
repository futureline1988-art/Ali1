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
remote server/device/audit data is read verbatim from
:class:`~developer_suite.admin.client.AdminApiClient`. This module's
only job is presentation-shaping: turning several services' outputs
into one :class:`DashboardSnapshot` a UI page can render without
itself importing five different services.

Phase 12 adds two presentation-only derivations, neither of which is a
new business rule:

* **Issuance vs. renewal.** :class:`~developer_suite.models.license.IssuedLicense`
  has no ``is_renewal`` flag (adding one would be new persisted
  business state, which Phase 12 explicitly must not introduce — "do
  not duplicate models"). Instead, :func:`_split_issuances_and_renewals`
  reads the two timestamps every model already carries
  (:class:`~models.base.TimestampMixin`'s ``created_at``/``updated_at``,
  the latter bumped by the ORM "on every flush that changes the row"):
  a license whose ``updated_at`` is materially later than its
  ``created_at`` has been renewed at least once since issuance
  (:meth:`~developer_suite.services.license_service.LicenseService.renew_license`
  mutates the same row rather than creating a new one); otherwise it is
  still showing its original issuance.
* **Chart aggregation.** :attr:`DashboardSnapshot.customer_growth`,
  :attr:`DashboardSnapshot.license_distribution`,
  :attr:`DashboardSnapshot.sync_activity_by_status`, and
  :attr:`DashboardSnapshot.expiration_timeline` are all bucketed here
  from data the existing services already return — grouping/counting,
  not a new source of truth.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from developer_suite.admin.client import AdminApiClient, AdminApiError, AuditLogEntry, SyncActivityEntry
from developer_suite.config import DeveloperSuiteConfig
from developer_suite.models.customer import Customer, CustomerStatus
from developer_suite.models.license import IssuedLicense
from developer_suite.services.customer_service import CustomerService
from developer_suite.services.license_service import LicenseService
from developer_suite.sync.scheduler import SyncSchedulerService
from licensing.enums import LicenseType

_DEFAULT_UPCOMING_EXPIRATION_WINDOW_DAYS = 30
_DEFAULT_RECENT_ITEMS_LIMIT = 8
_DEFAULT_CHART_MONTHS_WINDOW = 6
#: How much later ``updated_at`` must be than ``created_at`` before a
#: license is considered renewed rather than merely just-created (see
#: this module's own docstring) — comfortably larger than the
#: sub-second gap between the two ``TimestampMixin`` defaults firing on
#: the same insert.
_RENEWAL_DETECTION_THRESHOLD = timedelta(seconds=5)

#: Authentication-lifecycle actions shown in the narrower "recent
#: authentication events" panel; the full action set (including
#: password change/reset) still appears in the unfiltered
#: :attr:`DashboardSnapshot.recent_audit_log`.
_AUTHENTICATION_EVENT_ACTIONS = frozenset({"login", "login_failed", "logout", "account_locked"})


@dataclass(frozen=True)
class UpcomingExpiration:
    """One active license expiring soon, for the dashboard's expiration list."""

    customer_name: str
    license_type_label: str
    expires_at: date
    days_remaining: int


@dataclass(frozen=True)
class RecentCustomerRegistration:
    """One recently registered customer, for the "recent registrations" panel."""

    company_name: str
    registered_at: datetime


@dataclass(frozen=True)
class RecentLicenseEvent:
    """One recent license issuance or renewal, for their respective panels."""

    customer_name: str
    license_type_label: str
    event_at: datetime


@dataclass(frozen=True)
class RecentServerEvent:
    """One recently registered installation, for the "recent server events" panel."""

    device_name: str
    device_type: str
    registered_at: datetime


@dataclass(frozen=True)
class CustomerGrowthPoint:
    """Cumulative registered-customer count as of one calendar month."""

    month: str
    """``"YYYY-MM"``, chronological order."""
    cumulative_customers: int


@dataclass(frozen=True)
class LicenseDistributionEntry:
    """How many issued licenses fall under one plan."""

    license_type_label: str
    count: int


@dataclass(frozen=True)
class SyncActivityBucket:
    """How many recent change records fell under one outcome status."""

    status_label: str
    count: int


@dataclass(frozen=True)
class ExpirationTimelineBucket:
    """How many active licenses expire in one calendar month."""

    month: str
    """``"YYYY-MM"``, chronological order."""
    count: int


@dataclass(frozen=True)
class DashboardSnapshot:
    """Everything the Developer Dashboard displays, computed once per refresh.

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
        connected_devices: Every registered device (any device type —
            Attendance Client installations *and* Developer Suite
            installations) currently online; ``None`` under the same
            conditions as :attr:`online_companies`.
        active_licenses: Licenses currently in good standing (see
            :attr:`~developer_suite.models.license.IssuedLicense.is_active`).
        expired_licenses: Licenses past their expiration date.
        trial_licenses: Licenses of :attr:`~licensing.enums.LicenseType.TRIAL`.
        monthly_licenses: Licenses of :attr:`~licensing.enums.LicenseType.MONTHLY`.
        yearly_licenses: Licenses of :attr:`~licensing.enums.LicenseType.YEARLY`.
        lifetime_licenses: Licenses of :attr:`~licensing.enums.LicenseType.LIFETIME`.
        upcoming_expirations: Active licenses expiring within the
            configured window, soonest first.
        last_sync_at: This installation's last successful synchronization.
        pending_sync_count: Local changes still queued to push.
        server_reachable: Whether ``/health`` answered; ``None`` if
            not yet checked.
        server_version: The Attendance Server's own reported version,
            if reachable.
        database_connected: Whether the Attendance Server's own
            database answered a live connectivity check; ``None`` if
            this could not be determined (no admin token configured, or
            the server is unreachable).
        platform_version: This Developer Suite installation's own
            version.
        recent_customer_registrations: The most recently registered
            customers, most recent first.
        recent_license_issuances: The most recently *first-issued*
            licenses (see this module's docstring on the
            issuance/renewal split), most recent first.
        recent_license_renewals: The most recently *renewed* licenses,
            most recent first.
        recent_synchronization: The most recent entries from the
            Attendance Server's own change ledger, across every
            installation — reused verbatim from
            :meth:`~developer_suite.admin.client.AdminApiClient.list_recent_activity`.
        recent_server_events: The most recently registered
            installations (Attendance Client or Developer Suite),
            most recent first — a device registration is the one kind
            of "server event" this platform currently records outside
            the sync ledger and the auth audit trail.
        recent_authentication_events: The most recent login-lifecycle
            audit events (login, failed login, logout, lockout), most
            recent first.
        recent_audit_log: The complete recent admin audit trail
            (authentication events plus password change/reset/session
            revocation), most recent first — reused verbatim from
            :meth:`~developer_suite.admin.client.AdminApiClient.list_audit_log`.
        customer_growth: Cumulative customer count by month, oldest
            first, for the customer-growth chart.
        license_distribution: Issued-license count per plan, for the
            license-distribution chart.
        sync_activity_by_status: Recent change-record count per
            outcome status, for the synchronization-activity chart.
        expiration_timeline: Active-license expiration count per
            month, for the license-expiration-timeline chart.
        latest_deployed_version: The highest software update version
            with at least one company reporting a successful install
            (Phase 14 — see
            :class:`~server.services.update_service.UpdateDashboardStats`);
            ``None`` if no company has installed one yet, or this
            could not be determined (no admin token configured, or
            the server is unreachable).
        companies_per_version: ``{version: installed_company_count}``
            for every software update version with at least one
            installed company.
        pending_updates_count: Companies currently anywhere between
            "pending" and "verified" for any software update version.
        failed_updates_count: Companies whose most recent software
            update report is a failure.
        successful_updates_count: Companies whose most recent software
            update report is a successful install.
        average_update_download_progress_percent: Mean download
            progress among companies currently downloading a software
            update; ``None`` if none are, or this could not be
            determined.
    """

    total_customers: int = 0
    active_customers: int = 0
    suspended_customers: int = 0
    online_companies: int | None = None
    offline_companies: int | None = None
    connected_devices: int | None = None
    active_licenses: int = 0
    expired_licenses: int = 0
    trial_licenses: int = 0
    monthly_licenses: int = 0
    yearly_licenses: int = 0
    lifetime_licenses: int = 0
    upcoming_expirations: list[UpcomingExpiration] = field(default_factory=list)
    last_sync_at: datetime | None = None
    pending_sync_count: int = 0
    server_reachable: bool | None = None
    server_version: str | None = None
    database_connected: bool | None = None
    platform_version: str = ""
    recent_customer_registrations: list[RecentCustomerRegistration] = field(default_factory=list)
    recent_license_issuances: list[RecentLicenseEvent] = field(default_factory=list)
    recent_license_renewals: list[RecentLicenseEvent] = field(default_factory=list)
    recent_synchronization: list[SyncActivityEntry] = field(default_factory=list)
    recent_server_events: list[RecentServerEvent] = field(default_factory=list)
    recent_authentication_events: list[AuditLogEntry] = field(default_factory=list)
    recent_audit_log: list[AuditLogEntry] = field(default_factory=list)
    customer_growth: list[CustomerGrowthPoint] = field(default_factory=list)
    license_distribution: list[LicenseDistributionEntry] = field(default_factory=list)
    sync_activity_by_status: list[SyncActivityBucket] = field(default_factory=list)
    expiration_timeline: list[ExpirationTimelineBucket] = field(default_factory=list)
    latest_deployed_version: str | None = None
    companies_per_version: dict[str, int] = field(default_factory=dict)
    pending_updates_count: int = 0
    failed_updates_count: int = 0
    successful_updates_count: int = 0
    average_update_download_progress_percent: float | None = None


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
        recent_items_limit: int = _DEFAULT_RECENT_ITEMS_LIMIT,
        chart_months_window: int = _DEFAULT_CHART_MONTHS_WINDOW,
    ) -> None:
        """Create a dashboard service over already-constructed dependencies.

        Args:
            customer_service: Source of customer counts and recent
                registrations.
            license_service: Source of license counts, expirations,
                and recent issuance/renewal.
            sync_scheduler: Source of this installation's own sync
                status.
            admin_client: Source of remote device/server/audit data —
                every call is wrapped so an unreachable server or a
                missing admin token degrades those specific fields to
                ``None``/empty rather than failing the whole snapshot.
            config: Supplies :attr:`~developer_suite.config.DeveloperSuiteConfig.app_version`.
            upcoming_expiration_window_days: How many days ahead counts
                as "upcoming" for :attr:`DashboardSnapshot.upcoming_expirations`.
            recent_items_limit: How many entries each "recent ..." list
                keeps.
            chart_months_window: How many trailing calendar months
                :attr:`DashboardSnapshot.customer_growth` and
                :attr:`DashboardSnapshot.expiration_timeline` cover.
        """
        self._customer_service = customer_service
        self._license_service = license_service
        self._sync_scheduler = sync_scheduler
        self._admin_client = admin_client
        self._config = config
        self._upcoming_window_days = upcoming_expiration_window_days
        self._recent_items_limit = recent_items_limit
        self._chart_months_window = chart_months_window

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
        active_licenses = sum(1 for record in licenses if record.is_active)
        expired_licenses = sum(1 for record in licenses if record.is_expired)
        license_counts = Counter(record.license_type for record in licenses)
        upcoming_expirations = _upcoming_expirations(licenses, window_days=self._upcoming_window_days)
        issuances, renewals = _split_issuances_and_renewals(licenses, limit=self._recent_items_limit)

        sync_status = self._sync_scheduler.get_status()

        online_companies, offline_companies, connected_devices = self._count_devices_by_connectivity()
        server_reachable, server_version, database_connected = self._probe_server()
        recent_synchronization = self._recent_synchronization()
        recent_server_events = self._recent_server_events()
        recent_authentication_events, recent_audit_log = self._recent_audit_events()
        update_stats = self._update_stats()

        return DashboardSnapshot(
            total_customers=total_customers,
            active_customers=active_customers,
            suspended_customers=total_customers - active_customers,
            online_companies=online_companies,
            offline_companies=offline_companies,
            connected_devices=connected_devices,
            active_licenses=active_licenses,
            expired_licenses=expired_licenses,
            trial_licenses=license_counts.get(LicenseType.TRIAL, 0),
            monthly_licenses=license_counts.get(LicenseType.MONTHLY, 0),
            yearly_licenses=license_counts.get(LicenseType.YEARLY, 0),
            lifetime_licenses=license_counts.get(LicenseType.LIFETIME, 0),
            upcoming_expirations=upcoming_expirations,
            last_sync_at=sync_status.last_success_at,
            pending_sync_count=sync_status.pending_changes_count,
            server_reachable=server_reachable,
            server_version=server_version,
            database_connected=database_connected,
            platform_version=self._config.app_version,
            recent_customer_registrations=_recent_customer_registrations(
                customers, limit=self._recent_items_limit
            ),
            recent_license_issuances=issuances,
            recent_license_renewals=renewals,
            recent_synchronization=recent_synchronization,
            recent_server_events=recent_server_events,
            recent_authentication_events=recent_authentication_events,
            recent_audit_log=recent_audit_log,
            customer_growth=_customer_growth(customers, months_window=self._chart_months_window),
            license_distribution=_license_distribution(license_counts),
            sync_activity_by_status=_sync_activity_by_status(recent_synchronization),
            expiration_timeline=_expiration_timeline(licenses, months_window=self._chart_months_window),
            latest_deployed_version=update_stats.latest_deployed_version if update_stats else None,
            companies_per_version=update_stats.companies_per_version if update_stats else {},
            pending_updates_count=update_stats.pending_count if update_stats else 0,
            failed_updates_count=update_stats.failed_count if update_stats else 0,
            successful_updates_count=update_stats.successful_count if update_stats else 0,
            average_update_download_progress_percent=(
                update_stats.average_download_progress_percent if update_stats else None
            ),
        )

    def _count_devices_by_connectivity(self) -> tuple[int | None, int | None, int | None]:
        try:
            devices = self._admin_client.list_devices()
        except AdminApiError:
            return None, None, None
        client_devices = [device for device in devices if device.device_type == "attendance_client"]
        online_clients = sum(1 for device in client_devices if device.is_online())
        connected_devices = sum(1 for device in devices if device.is_online())
        return online_clients, len(client_devices) - online_clients, connected_devices

    def _probe_server(self) -> tuple[bool, str | None, bool | None]:
        reachable = self._admin_client.check_health()
        if not reachable:
            return False, None, None
        version_info = self._admin_client.get_version()
        server_version = version_info["app_version"] if version_info else None
        try:
            status = self._admin_client.get_server_status()
        except AdminApiError:
            return True, server_version, None
        return True, server_version, status.database_connected

    def _recent_synchronization(self) -> list[SyncActivityEntry]:
        try:
            return self._admin_client.list_recent_activity(limit=self._recent_items_limit)
        except AdminApiError:
            return []

    def _recent_server_events(self) -> list[RecentServerEvent]:
        try:
            devices = self._admin_client.list_devices()
        except AdminApiError:
            return []
        ordered = sorted(devices, key=lambda device: device.created_at, reverse=True)
        return [
            RecentServerEvent(
                device_name=device.name, device_type=device.device_type, registered_at=device.created_at
            )
            for device in ordered[: self._recent_items_limit]
        ]

    def _recent_audit_events(self) -> tuple[list[AuditLogEntry], list[AuditLogEntry]]:
        try:
            entries = self._admin_client.list_audit_log(limit=self._recent_items_limit)
        except AdminApiError:
            return [], []
        authentication_events = [
            entry for entry in entries if entry.action in _AUTHENTICATION_EVENT_ACTIONS
        ]
        return authentication_events, entries

    def _update_stats(self):
        try:
            return self._admin_client.get_update_stats()
        except AdminApiError:
            return None


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


def _recent_customer_registrations(
    customers: list[Customer], *, limit: int
) -> list[RecentCustomerRegistration]:
    """The most recently registered customers, most recent first."""
    ordered = sorted(customers, key=lambda customer: customer.created_at, reverse=True)
    return [
        RecentCustomerRegistration(company_name=customer.company_name, registered_at=customer.created_at)
        for customer in ordered[:limit]
    ]


def _split_issuances_and_renewals(
    licenses: list[IssuedLicense], *, limit: int
) -> tuple[list[RecentLicenseEvent], list[RecentLicenseEvent]]:
    """Split ``licenses`` into recent first-issuances and recent renewals.

    See this module's own docstring for how a renewal is detected from
    ``created_at``/``updated_at`` alone, with no new persisted field.
    """
    issuances: list[RecentLicenseEvent] = []
    renewals: list[RecentLicenseEvent] = []
    for record in licenses:
        was_renewed = (record.updated_at - record.created_at) > _RENEWAL_DETECTION_THRESHOLD
        event = RecentLicenseEvent(
            customer_name=record.customer.company_name,
            license_type_label=record.license_type.label_ar,
            event_at=record.updated_at if was_renewed else record.created_at,
        )
        (renewals if was_renewed else issuances).append(event)

    issuances.sort(key=lambda event: event.event_at, reverse=True)
    renewals.sort(key=lambda event: event.event_at, reverse=True)
    return issuances[:limit], renewals[:limit]


def _month_key(value: date | datetime) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _trailing_month_keys(*, months_window: int) -> list[str]:
    """The last ``months_window`` calendar months' keys, oldest first, ending this month."""
    today = datetime.now(timezone.utc).date()
    keys: list[str] = []
    year, month = today.year, today.month
    for _ in range(months_window):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(keys))


def _customer_growth(customers: list[Customer], *, months_window: int) -> list[CustomerGrowthPoint]:
    """Cumulative customer count as of each of the trailing ``months_window`` months."""
    month_keys = _trailing_month_keys(months_window=months_window)
    registrations_by_month = Counter(_month_key(customer.created_at) for customer in customers)

    earliest_shown = month_keys[0]
    cumulative = sum(count for month, count in registrations_by_month.items() if month < earliest_shown)

    points: list[CustomerGrowthPoint] = []
    for month in month_keys:
        cumulative += registrations_by_month.get(month, 0)
        points.append(CustomerGrowthPoint(month=month, cumulative_customers=cumulative))
    return points


def _license_distribution(license_counts: "Counter[LicenseType]") -> list[LicenseDistributionEntry]:
    """One entry per :class:`~licensing.enums.LicenseType`, in a stable, fixed order."""
    return [
        LicenseDistributionEntry(
            license_type_label=license_type.label_ar, count=license_counts.get(license_type, 0)
        )
        for license_type in (
            LicenseType.TRIAL,
            LicenseType.MONTHLY,
            LicenseType.YEARLY,
            LicenseType.LIFETIME,
        )
    ]


def _sync_activity_by_status(entries: list[SyncActivityEntry]) -> list[SyncActivityBucket]:
    """Recent change-record count per outcome status, in a stable order."""
    counts = Counter(entry.status for entry in entries)
    return [SyncActivityBucket(status_label=status, count=count) for status, count in sorted(counts.items())]


def _expiration_timeline(
    licenses: list[IssuedLicense], *, months_window: int
) -> list[ExpirationTimelineBucket]:
    """Active-license expiration count per month, covering the coming ``months_window`` months."""
    month_keys = _trailing_month_keys(months_window=months_window)
    # Reuse the same window width, but looking forward from this month
    # rather than back — expirations are a future-facing timeline.
    first_year, first_month = (int(part) for part in month_keys[-1].split("-"))
    forward_keys: list[str] = []
    year, month = first_year, first_month
    for _ in range(months_window):
        forward_keys.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            month = 1
            year += 1

    expirations_by_month = Counter(
        _month_key(record.expires_at)
        for record in licenses
        if record.is_active and record.expires_at is not None
    )
    return [
        ExpirationTimelineBucket(month=month, count=expirations_by_month.get(month, 0))
        for month in forward_keys
    ]

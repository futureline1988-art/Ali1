"""Reporting & Analytics: read-only report assembly for the Developer Suite (Phase 15).

Introduces no new persisted state and no new business logic — every
report method here builds a plain ``(rows, columns)`` pair (the same
shape :mod:`utils.csv_export`/:mod:`utils.excel`/:mod:`utils.pdf`
already share, see :mod:`services.report_service`'s own docstring for
the Attendance Client's identical convention) purely from data
:class:`~developer_suite.services.customer_service.CustomerService`,
:class:`~developer_suite.services.subscription_service.SubscriptionService`,
:class:`~developer_suite.services.configuration_publish_service.ConfigurationPublishService`,
:class:`~developer_suite.services.dashboard_service.DashboardService`,
and :class:`~developer_suite.admin.client.AdminApiClient` already
expose. Filtering, searching, sorting, date-range restriction, and
grouping are all plain, stateless operations over an already-fetched
``list[dict]`` — the same "compute over already-loaded data" style
:meth:`~developer_suite.services.configuration_publish_service.ConfigurationPublishService.compare_pending_changes`
already established — never a new persisted query.

Every method that reaches the Attendance Server (the synchronization,
audit log, device, and update deployment reports) routes through
:meth:`ReportingService._call`, which wraps
:class:`~developer_suite.admin.client.AdminApiError` into
:class:`ReportingServiceError` — the same one-exception-type-for-the
-UI wrapper :meth:`~developer_suite.services.update_manager_service.UpdateManagerService._call`
already established in Phase 14. The customer and
configuration-publication-history reports never raise it: they only
read this installation's own local database (the subscription report
is the exception among the "local" reports — it also reaches the
Attendance Server, via :class:`~developer_suite.services.subscription_service.SubscriptionService`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Callable, TypeVar

from developer_suite.admin.client import AdminApiClient, AdminApiError, DeviceInfo, UpdateVersionInfo
from developer_suite.models.customer import CustomerStatus
from developer_suite.services.configuration_publish_service import ConfigurationPublishService
from developer_suite.services.customer_service import CustomerService
from developer_suite.services.dashboard_service import DashboardService, DashboardSnapshot
from developer_suite.services.subscription_service import SubscriptionService, SubscriptionServiceError

_T = TypeVar("_T")

#: How many rows to request from the Attendance Server's own
#: recent-activity/audit-log ledgers — the highest value those
#: endpoints already accept (see ``server/api/routers/sync.py``'s
#: ``_MAX_ACTIVITY_LIMIT`` / ``server/api/routers/auth.py``'s
#: ``_MAX_AUDIT_LOG_LIMIT``); a report simply asks for the largest page
#: those already-existing, unmodified endpoints allow rather than the
#: dashboard's much smaller default.
_MAX_LEDGER_ROWS = 200

_CUSTOMER_STATUS_LABELS_AR = {
    CustomerStatus.ACTIVE: "نشط",
    CustomerStatus.SUSPENDED: "موقوف",
}

_DEVICE_TYPE_LABELS_AR = {
    "attendance_client": "نظام الحضور",
    "developer_suite": "مجموعة المطورين",
}

_AUDIT_ACTION_LABELS_AR = {
    "login": "تسجيل دخول",
    "login_failed": "فشل تسجيل الدخول",
    "logout": "تسجيل خروج",
    "token_refresh": "تجديد الجلسة",
    "password_change": "تغيير كلمة المرور",
    "password_reset_requested": "طلب إعادة تعيين كلمة المرور",
    "password_reset_completed": "اكتمال إعادة تعيين كلمة المرور",
    "account_locked": "قفل الحساب",
    "session_revoked": "إلغاء الجلسة",
}

_UPDATE_TYPE_LABELS_AR = {
    "optional": "اختياري",
    "recommended": "موصى به",
    "critical": "حرج",
    "mandatory": "إلزامي",
}

_PUBLISH_STATUS_LABELS_AR = {
    "draft": "مسودة",
    "scheduled": "مجدول",
    "published": "منشور",
    "disabled": "معطل",
    "rolled_back": "تراجع",
}


class ReportingServiceError(Exception):
    """Base class for report-generation failures the UI should display."""


class ReportCategory(str, Enum):
    """The 8 report categories Phase 15 requires."""

    EXECUTIVE_DASHBOARD = "executive_dashboard"
    CUSTOMERS = "customers"
    SUBSCRIPTIONS = "subscriptions"
    SYNCHRONIZATION = "synchronization"
    UPDATE_DEPLOYMENT = "update_deployment"
    AUDIT_LOG = "audit_log"
    DEVICES = "devices"
    CONFIGURATION_PUBLICATIONS = "configuration_publications"


#: Arabic display label for each category, in catalogue order.
REPORT_CATEGORY_LABELS_AR: dict[ReportCategory, str] = {
    ReportCategory.EXECUTIVE_DASHBOARD: "التقرير التنفيذي",
    ReportCategory.CUSTOMERS: "تقرير العملاء",
    ReportCategory.SUBSCRIPTIONS: "تقرير الاشتراكات",
    ReportCategory.SYNCHRONIZATION: "تقرير المزامنة",
    ReportCategory.UPDATE_DEPLOYMENT: "تقرير نشر التحديثات",
    ReportCategory.AUDIT_LOG: "تقرير سجل التدقيق",
    ReportCategory.DEVICES: "تقرير الأجهزة",
    ReportCategory.CONFIGURATION_PUBLICATIONS: "تقرير سجل نشر الإعدادات",
}

#: Which row key each category's date-range filter applies to; ``None``
#: for the one category (the executive summary) that is not a list of
#: dated events at all.
_REPORT_DATE_FIELDS: dict[ReportCategory, str | None] = {
    ReportCategory.EXECUTIVE_DASHBOARD: None,
    ReportCategory.CUSTOMERS: "created_at",
    ReportCategory.SUBSCRIPTIONS: "created_at",
    ReportCategory.SYNCHRONIZATION: "created_at",
    ReportCategory.UPDATE_DEPLOYMENT: "reported_at",
    ReportCategory.AUDIT_LOG: "created_at",
    ReportCategory.DEVICES: "created_at",
    ReportCategory.CONFIGURATION_PUBLICATIONS: "created_at",
}


@dataclass(frozen=True)
class ReportFilters:
    """Every filter/sort control the Reporting page offers, for one report request.

    Attributes:
        search: A case-insensitive substring matched against every
            column's rendered value; blank matches everything.
        start_date: Only rows whose report-specific date field (see
            :data:`_REPORT_DATE_FIELDS`) falls on or after this date.
        end_date: Only rows whose date field falls on or before this
            date.
        sort_by: A column key from the report's own column spec to
            sort by; ``None`` keeps each report's natural order
            (almost always "most recent first").
        sort_descending: Sort direction when :attr:`sort_by` is set.
    """

    search: str = ""
    start_date: date | None = None
    end_date: date | None = None
    sort_by: str | None = None
    sort_descending: bool = False


@dataclass(frozen=True)
class ReportResult:
    """One assembled report, ready to display or export.

    Attributes:
        rows: Row data, already filtered/sorted — the exact shape
            :func:`utils.csv_export.export_to_csv`/
            :func:`utils.excel.export_to_excel`/:func:`utils.pdf.export_to_pdf`
            expect.
        columns: Ordered ``(field_key, header_label)`` pairs, matching
            :attr:`rows`.
        total_before_filters: How many rows existed before
            :class:`ReportFilters` was applied — lets the UI show
            "showing 12 of 48" without a second query.
    """

    rows: list[dict[str, Any]] = field(default_factory=list)
    columns: list[tuple[str, str]] = field(default_factory=list)
    total_before_filters: int = 0


def _as_date(value: Any) -> date | None:
    """Coerce a row's raw date/datetime value to a plain :class:`date`, or ``None``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _stringify_dates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Render every ``date``/``datetime`` value in ``rows`` as a plain display string.

    Applied once, as the very last step before a :class:`ReportResult`
    is returned — filtering/sorting (see :func:`filter_rows`/
    :func:`sort_rows`) must run *before* this on the real ``date``/
    ``datetime`` objects, both for correct date-range comparison and
    for correct chronological (rather than lexicographic) sorting.

    Exists because :func:`utils.excel.export_to_excel` writes a native
    Excel date cell for any ``datetime``/``date`` value handed to it —
    which openpyxl rejects outright for a timezone-*aware* ``datetime``
    (every timestamp this module's data sources return, all reused
    verbatim from UTC-stamped columns/API responses). The Attendance
    Client's own :mod:`services.report_service` never hits this: it
    always pre-formats via :func:`utils.i18n.format_date`/``format_time``
    before building a row. This module deliberately does *not* reuse
    those two functions — both read :func:`config.get_config`, the
    Attendance Client's own locale setting, which would be exactly the
    cross-application coupling this phase's own "keep the three
    applications completely isolated" rule forbids — a plain, fixed
    ``strftime`` format is used instead.
    """
    formatted: list[dict[str, Any]] = []
    for row in rows:
        new_row = dict(row)
        for key, value in row.items():
            if isinstance(value, datetime):
                new_row[key] = value.strftime("%Y-%m-%d %H:%M")
            elif isinstance(value, date):
                new_row[key] = value.strftime("%Y-%m-%d")
        formatted.append(new_row)
    return formatted


def filter_rows(
    rows: list[dict[str, Any]], *, search: str = "", date_field: str | None = None,
    start_date: date | None = None, end_date: date | None = None,
) -> list[dict[str, Any]]:
    """Filter ``rows`` by a case-insensitive substring and/or an inclusive date range.

    Args:
        rows: The unfiltered row dicts.
        search: A substring matched against every value in each row
            (case-insensitively); blank matches every row.
        date_field: Which key in each row holds the date/datetime to
            range-filter on; ``None`` skips date filtering entirely.
        start_date: Only rows whose ``date_field`` value is on or
            after this date; ``None`` leaves the lower bound open.
        end_date: Only rows whose ``date_field`` value is on or before
            this date; ``None`` leaves the upper bound open.

    Returns:
        The matching rows, in their original relative order.
    """
    needle = search.strip().lower()
    result = rows
    if needle:
        result = [
            row for row in result
            if any(needle in str(value).lower() for value in row.values() if value is not None)
        ]
    if date_field is not None and (start_date is not None or end_date is not None):
        filtered: list[dict[str, Any]] = []
        for row in result:
            row_date = _as_date(row.get(date_field))
            if row_date is None:
                continue
            if start_date is not None and row_date < start_date:
                continue
            if end_date is not None and row_date > end_date:
                continue
            filtered.append(row)
        result = filtered
    return result


def sort_rows(rows: list[dict[str, Any]], *, sort_by: str | None, descending: bool = False) -> list[dict[str, Any]]:
    """Sort ``rows`` by one column key, ``None`` values always sorting last.

    Args:
        rows: The rows to sort.
        sort_by: A row dict key to sort by; ``None`` returns ``rows``
            unchanged (each report's own natural order).
        descending: Sort direction.

    Returns:
        A new, sorted list; ``rows`` itself is never mutated.
    """
    if sort_by is None:
        return list(rows)

    # Split into present/missing first, rather than folding a presence
    # flag into a single sort key: with reverse=True a folded flag would
    # flip along with the real value, putting missing rows *first* on a
    # descending sort - never what a user wants. Missing rows always sort
    # last, independent of direction.
    present = [row for row in rows if row.get(sort_by) is not None]
    missing = [row for row in rows if row.get(sort_by) is None]
    present.sort(key=lambda row: row[sort_by], reverse=descending)
    return present + missing


def group_and_count(rows: list[dict[str, Any]], *, group_by: str, label: str = "الفئة") -> ReportResult:
    """Collapse ``rows`` into a ``(group value, count)`` breakdown, most frequent first.

    Reused by the Reporting page's "group by" control and by
    :mod:`developer_suite.ui.reporting_charts`'s generic bar chart —
    the same small table can drive either a preview or a chart.

    Args:
        rows: Already-filtered rows to group.
        group_by: Which row dict key to group on; a row missing this
            key groups under an explicit "غير محدد" ("unspecified")
            bucket rather than being silently dropped.
        label: The header label for the group-value column.
    """
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(group_by)
        key = str(value) if value is not None else "غير محدد"
        counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    return ReportResult(
        rows=[{"group": group, "count": count} for group, count in ordered],
        columns=[("group", label), ("count", "العدد")],
        total_before_filters=len(rows),
    )


class ReportingService:
    """Assembles, filters, sorts, and groups every Phase 15 report category."""

    def __init__(
        self,
        customer_service: CustomerService,
        subscription_service: SubscriptionService,
        configuration_publish_service: ConfigurationPublishService,
        dashboard_service: DashboardService,
        admin_client: AdminApiClient,
    ) -> None:
        """Create a reporting service over already-constructed dependencies.

        Args:
            customer_service: Source of the customer report.
            subscription_service: Source of the subscription report.
            configuration_publish_service: Source of the configuration
                publication history report.
            dashboard_service: Source of the executive dashboard
                report (reuses :meth:`~developer_suite.services.dashboard_service.DashboardService.get_snapshot`
                verbatim).
            admin_client: Source of the synchronization, audit log,
                device, and update deployment reports.
        """
        self._customer_service = customer_service
        self._subscription_service = subscription_service
        self._configuration_publish_service = configuration_publish_service
        self._dashboard_service = dashboard_service
        self._admin_client = admin_client

    @staticmethod
    def _call(operation: Callable[[], _T]) -> _T:
        """Invoke one :class:`~developer_suite.admin.client.AdminApiClient` call, translating its errors."""
        try:
            return operation()
        except AdminApiError as exc:
            raise ReportingServiceError(str(exc)) from exc

    def _finish(
        self, category: ReportCategory, rows: list[dict[str, Any]], columns: list[tuple[str, str]],
        filters: ReportFilters,
    ) -> ReportResult:
        """Apply ``filters`` (search, date range, sort) to freshly assembled rows."""
        filtered = filter_rows(
            rows,
            search=filters.search,
            date_field=_REPORT_DATE_FIELDS[category],
            start_date=filters.start_date,
            end_date=filters.end_date,
        )
        sorted_rows = sort_rows(filtered, sort_by=filters.sort_by, descending=filters.sort_descending)
        return ReportResult(
            rows=_stringify_dates(sorted_rows), columns=columns, total_before_filters=len(rows)
        )

    # -- Report builders, one per category -----------------------------------

    def build_report(self, category: ReportCategory, filters: ReportFilters | None = None) -> ReportResult:
        """Build one report by category — the single entry point the UI calls."""
        filters = filters or ReportFilters()
        builder = {
            ReportCategory.EXECUTIVE_DASHBOARD: self.build_executive_dashboard_report,
            ReportCategory.CUSTOMERS: self.build_customer_report,
            ReportCategory.SUBSCRIPTIONS: self.build_subscription_report,
            ReportCategory.SYNCHRONIZATION: self.build_synchronization_report,
            ReportCategory.UPDATE_DEPLOYMENT: self.build_update_deployment_report,
            ReportCategory.AUDIT_LOG: self.build_audit_log_report,
            ReportCategory.DEVICES: self.build_device_report,
            ReportCategory.CONFIGURATION_PUBLICATIONS: self.build_configuration_publication_report,
        }[category]
        return builder(filters)

    def build_executive_dashboard_report(self, filters: ReportFilters | None = None) -> ReportResult:
        """The Executive Dashboard report: every KPI from :meth:`DashboardService.get_snapshot` as one table.

        Unlike every other report category, this one is not a list of
        dated events, so :attr:`ReportFilters.start_date`/``end_date``
        never apply (see :data:`_REPORT_DATE_FIELDS`); only
        :attr:`~ReportFilters.search` and :attr:`~ReportFilters.sort_by`
        are meaningful here.
        """
        filters = filters or ReportFilters()
        snapshot = self._dashboard_service.get_snapshot()
        rows = _executive_dashboard_rows(snapshot)
        columns = [("metric", "المؤشر"), ("value", "القيمة")]
        return self._finish(ReportCategory.EXECUTIVE_DASHBOARD, rows, columns, filters)

    def build_customer_report(self, filters: ReportFilters | None = None) -> ReportResult:
        """The Customer report: every registered customer."""
        filters = filters or ReportFilters()
        customers = self._customer_service.search_customers("")
        rows = [
            {
                "company_name": customer.company_name,
                "contact_name": customer.contact_name,
                "phone": customer.phone or "",
                "email": customer.email or "",
                "status": _CUSTOMER_STATUS_LABELS_AR.get(customer.status, customer.status.value),
                "created_at": customer.created_at,
            }
            for customer in customers
        ]
        columns = [
            ("company_name", "اسم الشركة"),
            ("contact_name", "جهة الاتصال"),
            ("phone", "الهاتف"),
            ("email", "البريد الإلكتروني"),
            ("status", "الحالة"),
            ("created_at", "تاريخ التسجيل"),
        ]
        return self._finish(ReportCategory.CUSTOMERS, rows, columns, filters)

    def build_subscription_report(self, filters: ReportFilters | None = None) -> ReportResult:
        """The Subscription report: every company subscription, with its current standing.

        Raises:
            ReportingServiceError: The Attendance Server could not be
                reached (subscriptions are stored there, not locally —
                see :mod:`server.models.subscription`).
        """
        filters = filters or ReportFilters()
        try:
            subscriptions = self._subscription_service.list_subscriptions()
        except SubscriptionServiceError as exc:
            raise ReportingServiceError(str(exc)) from exc
        rows = [
            {
                "company_name": record.company_name,
                "status": _subscription_status_label(record.status, record.is_expired),
                "created_at": record.created_at,
                "subscription_start_date": record.subscription_start_date,
                "subscription_end_date": record.subscription_end_date,
                "days_remaining": record.days_remaining,
                "max_devices": record.max_devices,
                "max_users": record.max_users if record.max_users is not None else "بلا حدود",
                "device_count": record.device_count if record.device_count is not None else "",
            }
            for record in subscriptions
        ]
        columns = [
            ("company_name", "اسم الشركة"),
            ("status", "الحالة"),
            ("subscription_start_date", "تاريخ البدء"),
            ("subscription_end_date", "تاريخ الانتهاء"),
            ("days_remaining", "الأيام المتبقية"),
            ("max_devices", "الحد الأقصى للأجهزة"),
            ("device_count", "عدد الأجهزة الحالي"),
            ("max_users", "الحد الأقصى للمستخدمين"),
        ]
        return self._finish(ReportCategory.SUBSCRIPTIONS, rows, columns, filters)

    def build_synchronization_report(self, filters: ReportFilters | None = None) -> ReportResult:
        """The Synchronization report: the Attendance Server's own recent change ledger."""
        filters = filters or ReportFilters()
        entries = self._call(lambda: self._admin_client.list_recent_activity(limit=_MAX_LEDGER_ROWS))
        rows = [
            {
                "id": entry.id,
                "entity_type": entry.entity_type,
                "entity_id": entry.entity_id,
                "operation": entry.operation,
                "status": entry.status,
                "conflict_reason": entry.conflict_reason or "",
                "device_id": entry.device_id,
                "created_at": entry.created_at,
            }
            for entry in entries
        ]
        columns = [
            ("id", "المعرف"),
            ("entity_type", "نوع الكيان"),
            ("entity_id", "معرف الكيان"),
            ("operation", "العملية"),
            ("status", "الحالة"),
            ("conflict_reason", "سبب التعارض"),
            ("device_id", "معرف الجهاز"),
            ("created_at", "التاريخ"),
        ]
        return self._finish(ReportCategory.SYNCHRONIZATION, rows, columns, filters)

    def build_update_deployment_report(self, filters: ReportFilters | None = None) -> ReportResult:
        """The Update Deployment report: which device is on which version, and its status.

        Cross-references the raw ``device_public_id``/``update_version_id``
        every status row carries against :meth:`~developer_suite.admin.client.AdminApiClient.list_devices`/
        :meth:`~developer_suite.admin.client.AdminApiClient.list_update_versions`
        (both already being fetched here) — the same client-side
        resolution :class:`~developer_suite.services.dashboard_service.DashboardService`
        already relies on, never a server-side join.
        """
        filters = filters or ReportFilters()
        statuses = self._call(self._admin_client.list_update_device_statuses)
        versions = self._call(self._admin_client.list_update_versions)
        devices = self._call(self._admin_client.list_devices)
        version_by_id: dict[int, UpdateVersionInfo] = {version.id: version for version in versions}
        device_by_public_id: dict[str, DeviceInfo] = {device.public_id: device for device in devices}

        rows = []
        for status in statuses:
            version = version_by_id.get(status.update_version_id)
            device = device_by_public_id.get(status.device_public_id)
            rows.append(
                {
                    "device_name": device.name if device is not None else status.device_public_id,
                    "version": version.version if version is not None else str(status.update_version_id),
                    "update_type": _UPDATE_TYPE_LABELS_AR.get(
                        version.update_type, version.update_type
                    ) if version is not None else "",
                    "status": status.status,
                    "progress_percent": status.progress_percent,
                    "error_message": status.error_message or "",
                    "reported_at": status.reported_at,
                }
            )
        columns = [
            ("device_name", "الجهاز"),
            ("version", "الإصدار"),
            ("update_type", "نوع التحديث"),
            ("status", "الحالة"),
            ("progress_percent", "نسبة التقدم"),
            ("error_message", "رسالة الخطأ"),
            ("reported_at", "آخر تحديث"),
        ]
        return self._finish(ReportCategory.UPDATE_DEPLOYMENT, rows, columns, filters)

    def build_audit_log_report(self, filters: ReportFilters | None = None) -> ReportResult:
        """The Audit Log report: the Attendance Server's own admin authentication audit trail."""
        filters = filters or ReportFilters()
        entries = self._call(lambda: self._admin_client.list_audit_log(limit=_MAX_LEDGER_ROWS))
        rows = [
            {
                "public_id": entry.public_id,
                "action": _AUDIT_ACTION_LABELS_AR.get(entry.action, entry.action),
                "description": entry.description or "",
                "created_at": entry.created_at,
            }
            for entry in entries
        ]
        columns = [
            ("public_id", "المعرف"),
            ("action", "الإجراء"),
            ("description", "التفاصيل"),
            ("created_at", "التاريخ"),
        ]
        return self._finish(ReportCategory.AUDIT_LOG, rows, columns, filters)

    def build_device_report(self, filters: ReportFilters | None = None) -> ReportResult:
        """The Device report: every device registered with the Attendance Server."""
        filters = filters or ReportFilters()
        devices = self._call(self._admin_client.list_devices)
        rows = [
            {
                "name": device.name,
                "device_type": _DEVICE_TYPE_LABELS_AR.get(device.device_type, device.device_type),
                "is_active": "نعم" if device.is_active else "لا",
                "is_online": "متصل" if device.is_online() else "غير متصل",
                "last_seen_at": device.last_seen_at,
                "created_at": device.created_at,
            }
            for device in devices
        ]
        columns = [
            ("name", "اسم الجهاز"),
            ("device_type", "نوع الجهاز"),
            ("is_active", "مفعّل"),
            ("is_online", "الاتصال"),
            ("last_seen_at", "آخر ظهور"),
            ("created_at", "تاريخ التسجيل"),
        ]
        return self._finish(ReportCategory.DEVICES, rows, columns, filters)

    def build_configuration_publication_report(self, filters: ReportFilters | None = None) -> ReportResult:
        """The Configuration Publication History report: every publish, across every installation."""
        filters = filters or ReportFilters()
        publications = self._configuration_publish_service.list_all_publications()
        rows = [
            {
                "company_name": publication.customer.company_name,
                "target_device_public_id": publication.target_device_public_id,
                "version": publication.version,
                "published_by": publication.published_by,
                "change_summary": publication.change_summary or "",
                "checksum": publication.checksum,
                "created_at": publication.created_at,
            }
            for publication in publications
        ]
        columns = [
            ("company_name", "اسم الشركة"),
            ("target_device_public_id", "معرف الجهاز المستهدف"),
            ("version", "الإصدار"),
            ("published_by", "نُشر بواسطة"),
            ("change_summary", "ملخص التغيير"),
            ("checksum", "بصمة التحقق"),
            ("created_at", "تاريخ النشر"),
        ]
        return self._finish(ReportCategory.CONFIGURATION_PUBLICATIONS, rows, columns, filters)


def _subscription_status_label(status: str, is_expired: bool) -> str:
    """Mirror ``developer_suite/ui/subscription_management_page.py``'s status labeling exactly."""
    if is_expired:
        return "منتهي"
    if status == "suspended":
        return "موقوف"
    return "نشط"


def _executive_dashboard_rows(snapshot: DashboardSnapshot) -> list[dict[str, Any]]:
    """Flatten a :class:`DashboardSnapshot` into ``metric``/``value`` rows."""

    def _bool_or_unknown(value: bool | None) -> str:
        if value is None:
            return "غير معروف"
        return "نعم" if value else "لا"

    def _optional(value: Any) -> str:
        return "غير متاح" if value is None else str(value)

    entries: list[tuple[str, str]] = [
        ("إجمالي العملاء", str(snapshot.total_customers)),
        ("العملاء النشطون", str(snapshot.active_customers)),
        ("العملاء الموقوفون", str(snapshot.suspended_customers)),
        ("الشركات المتصلة", _optional(snapshot.online_companies)),
        ("الشركات غير المتصلة", _optional(snapshot.offline_companies)),
        ("الأجهزة المتصلة", _optional(snapshot.connected_devices)),
        ("إجمالي الاشتراكات", _optional(snapshot.total_subscriptions)),
        ("الاشتراكات النشطة", str(snapshot.active_subscriptions)),
        ("الاشتراكات الموقوفة", str(snapshot.suspended_subscriptions)),
        ("الاشتراكات المنتهية", str(snapshot.expired_subscriptions)),
        ("آخر مزامنة ناجحة", _optional(snapshot.last_sync_at)),
        ("التغييرات المعلقة", str(snapshot.pending_sync_count)),
        ("الخادم متاح", _bool_or_unknown(snapshot.server_reachable)),
        ("إصدار الخادم", _optional(snapshot.server_version)),
        ("قاعدة بيانات الخادم متصلة", _bool_or_unknown(snapshot.database_connected)),
        ("إصدار مجموعة المطورين", snapshot.platform_version),
        ("أحدث إصدار منشور", _optional(snapshot.latest_deployed_version)),
        ("تحديثات معلقة", str(snapshot.pending_updates_count)),
        ("تحديثات فاشلة", str(snapshot.failed_updates_count)),
        ("تحديثات ناجحة", str(snapshot.successful_updates_count)),
        (
            "متوسط تقدم تنزيل التحديث",
            _optional(
                f"{snapshot.average_update_download_progress_percent:.0f}%"
                if snapshot.average_update_download_progress_percent is not None
                else None
            ),
        ),
    ]
    return [{"metric": metric, "value": value} for metric, value in entries]

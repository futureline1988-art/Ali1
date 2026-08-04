"""The Developer Dashboard: the Developer Suite's main landing page.

Talks only to :class:`~developer_suite.services.dashboard_refresh_service.DashboardRefreshService`
(for data) and :class:`~developer_suite.ui.dashboard_quick_actions.QuickActionsPanel`
(for actions) — never to any repository, or to the customer/license/
sync services directly — matching this platform's established
service/UI boundary
(:class:`~developer_suite.ui.customer_management_page.CustomerManagementPage`'s
own docstring). Every number shown here comes from one
:class:`~developer_suite.services.dashboard_service.DashboardSnapshot`;
this page only lays widgets out.

Refreshing never touches this widget's thread directly: this page
never calls :meth:`~developer_suite.services.dashboard_service.DashboardService.get_snapshot`
itself (that would block the UI thread on local DB queries and remote
HTTP calls) — it only connects to
:attr:`~developer_suite.services.dashboard_refresh_service.DashboardRefreshService.snapshot_ready`,
a signal already delivered on the UI thread after the actual work ran
on a background :class:`~PySide6.QtCore.QThread`. The "تحديث" button
does not recompute anything synchronously either; it only asks the
refresh service to run one cycle sooner than its next scheduled tick.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from developer_suite.admin.client import AuditLogEntry, SyncActivityEntry
from developer_suite.services.customer_service import CustomerService
from developer_suite.services.dashboard_refresh_service import DashboardRefreshService
from developer_suite.services.dashboard_service import (
    DashboardSnapshot,
    RecentCustomerRegistration,
    RecentLicenseEvent,
    RecentServerEvent,
)
from developer_suite.services.license_service import LicenseService
from developer_suite.ui.dashboard_charts import (
    CustomerGrowthChart,
    ExpirationTimelineChart,
    LicenseDistributionChart,
    OnlineCompaniesChart,
    SyncActivityChart,
)
from developer_suite.ui.dashboard_quick_actions import QuickActionsPanel

_CARD_COLUMNS = 5
_CHART_COLUMNS = 2

_DEVICE_TYPE_LABELS_AR = {
    "attendance_client": "نظام الحضور",
    "developer_suite": "مجموعة المطورين",
}
_SYNC_OPERATION_LABELS_AR = {"create": "إنشاء", "update": "تعديل", "delete": "حذف"}
_SYNC_STATUS_LABELS_AR = {"applied": "مطبّق", "conflict": "تعارض", "rejected": "مرفوض"}
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


def _optional_count(value: int | None) -> str:
    return str(value) if value is not None else "غير متاح"


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "لم تتم بعد"
    return value.strftime("%Y-%m-%d %H:%M")


def _update_progress_label(value: float | None) -> str:
    return f"{value:.0f}%" if value is not None else "لا يوجد تنزيل حالياً"


def _server_status_label(snapshot: DashboardSnapshot) -> str:
    if snapshot.server_reachable is None:
        return "غير معروف"
    if not snapshot.server_reachable:
        return "غير متصل"
    return f"متصل (إصدار {snapshot.server_version or '؟'})"


def _database_status_label(snapshot: DashboardSnapshot) -> str:
    if snapshot.database_connected is None:
        return "غير معروف"
    return "متصلة" if snapshot.database_connected else "غير متصلة"


def _clear_layout(layout) -> None:
    """Remove and dispose of every widget currently in ``layout``."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


def _stat_tile(title: str, value: str) -> QGroupBox:
    """Build one labeled statistic tile."""
    box = QGroupBox(title)
    layout = QVBoxLayout(box)
    label = QLabel(value)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    font = label.font()
    font.setPointSize(16)
    font.setBold(True)
    label.setFont(font)
    layout.addWidget(label)
    return box


def _fill_list(list_widget: QListWidget, lines: list[str], *, empty_text: str) -> None:
    list_widget.clear()
    if not lines:
        list_widget.addItem(empty_text)
        return
    list_widget.addItems(lines)


def _customer_registration_lines(entries: list[RecentCustomerRegistration]) -> list[str]:
    return [f"{entry.company_name} — {_format_datetime(entry.registered_at)}" for entry in entries]


def _license_event_lines(entries: list[RecentLicenseEvent]) -> list[str]:
    return [
        f"{entry.customer_name} — {entry.license_type_label} — {_format_datetime(entry.event_at)}"
        for entry in entries
    ]


def _sync_activity_lines(entries: list[SyncActivityEntry]) -> list[str]:
    return [
        f"{entry.entity_type} #{entry.entity_id} — "
        f"{_SYNC_OPERATION_LABELS_AR.get(entry.operation, entry.operation)} — "
        f"{_SYNC_STATUS_LABELS_AR.get(entry.status, entry.status)} — "
        f"{_format_datetime(entry.created_at)}"
        for entry in entries
    ]


def _server_event_lines(entries: list[RecentServerEvent]) -> list[str]:
    return [
        f"{entry.device_name} ({_DEVICE_TYPE_LABELS_AR.get(entry.device_type, entry.device_type)}) — "
        f"{_format_datetime(entry.registered_at)}"
        for entry in entries
    ]


def _audit_log_lines(entries: list[AuditLogEntry]) -> list[str]:
    lines = []
    for entry in entries:
        label = _AUDIT_ACTION_LABELS_AR.get(entry.action, entry.action)
        text = f"{label} — {_format_datetime(entry.created_at)}"
        if entry.description:
            text = f"{text} — {entry.description}"
        lines.append(text)
    return lines


class DashboardPage(QWidget):
    """The main dashboard: platform-wide counts, activity, charts, and quick actions.

    Attributes:
        navigate_requested: Forwarded verbatim from
            :attr:`~developer_suite.ui.dashboard_quick_actions.QuickActionsPanel.navigate_requested`
            — :class:`~developer_suite.ui.main_window.MainWindow` connects
            to this on every module's page that exposes it, the same
            duck-typed convention it already applies to every module's
            :meth:`~developer_suite.modules.base.PlatformModule.build_page`.
    """

    navigate_requested = Signal(str)

    def __init__(
        self,
        refresh_service: DashboardRefreshService,
        customer_service: CustomerService,
        license_service: LicenseService,
        *,
        parent: QWidget | None = None,
    ) -> None:
        """Build the page and subscribe to the shared background refresh service.

        Args:
            refresh_service: Supplies every :class:`~developer_suite.services.dashboard_service.DashboardSnapshot`
                this page displays, computed off the UI thread (see
                this module's own docstring).
            customer_service: Passed through to :class:`~developer_suite.ui.dashboard_quick_actions.QuickActionsPanel`.
            license_service: Passed through to :class:`~developer_suite.ui.dashboard_quick_actions.QuickActionsPanel`.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._refresh_service = refresh_service

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        content = QWidget(scroll)
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel("لوحة التحكم", content)
        title_font = title.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        header.addWidget(title, stretch=1)

        self.refresh_button = QPushButton("تحديث", content)
        self.refresh_button.clicked.connect(self._refresh_service.refresh_now)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)

        self.quick_actions = QuickActionsPanel(customer_service, license_service, parent=content)
        self.quick_actions.action_completed.connect(self._refresh_service.refresh_now)
        self.quick_actions.navigate_requested.connect(self.navigate_requested)
        layout.addWidget(self.quick_actions)

        self._grid = QGridLayout()
        self._grid.setSpacing(10)
        layout.addLayout(self._grid)

        charts_box = QGroupBox("الرسوم البيانية", content)
        self._charts_grid = QGridLayout(charts_box)
        self._charts_grid.setSpacing(10)
        self.customer_growth_chart = CustomerGrowthChart(parent=charts_box)
        self.license_distribution_chart = LicenseDistributionChart(parent=charts_box)
        self.online_companies_chart = OnlineCompaniesChart(parent=charts_box)
        self.sync_activity_chart = SyncActivityChart(parent=charts_box)
        self.expiration_timeline_chart = ExpirationTimelineChart(parent=charts_box)
        for index, chart in enumerate(
            (
                self.customer_growth_chart,
                self.license_distribution_chart,
                self.online_companies_chart,
                self.sync_activity_chart,
                self.expiration_timeline_chart,
            )
        ):
            row, column = divmod(index, _CHART_COLUMNS)
            self._charts_grid.addWidget(chart, row, column)
        layout.addWidget(charts_box)

        activity_box = QGroupBox("النشاط الأخير", content)
        activity_layout = QVBoxLayout(activity_box)
        self.activity_tabs = QTabWidget(activity_box)
        self.registrations_list = QListWidget(self.activity_tabs)
        self.activity_tabs.addTab(self.registrations_list, "تسجيلات العملاء")
        self.issuances_list = QListWidget(self.activity_tabs)
        self.activity_tabs.addTab(self.issuances_list, "إصدار التراخيص")
        self.renewals_list = QListWidget(self.activity_tabs)
        self.activity_tabs.addTab(self.renewals_list, "تجديد التراخيص")
        self.synchronization_list = QListWidget(self.activity_tabs)
        self.activity_tabs.addTab(self.synchronization_list, "المزامنة")
        self.server_events_list = QListWidget(self.activity_tabs)
        self.activity_tabs.addTab(self.server_events_list, "أحداث الخادم")
        self.authentication_events_list = QListWidget(self.activity_tabs)
        self.activity_tabs.addTab(self.authentication_events_list, "أحداث المصادقة")
        self.audit_log_list = QListWidget(self.activity_tabs)
        self.activity_tabs.addTab(self.audit_log_list, "سجل التدقيق")
        activity_layout.addWidget(self.activity_tabs)
        layout.addWidget(activity_box)

        self.expirations_box = QGroupBox("تراخيص قاربت على الانتهاء", content)
        self.expirations_layout = QVBoxLayout(self.expirations_box)
        layout.addWidget(self.expirations_box)

        layout.addStretch(1)

        self._show_loading_placeholder()
        self._refresh_service.snapshot_ready.connect(self._populate)

    def _show_loading_placeholder(self) -> None:
        """Fill the cards grid with a loading placeholder until the first snapshot arrives."""
        self._grid.addWidget(_stat_tile("جارِ التحميل", "..."), 0, 0)

    def _populate(self, snapshot: DashboardSnapshot) -> None:
        """Fill every section from ``snapshot``, replacing current contents."""
        _clear_layout(self._grid)

        cards = (
            ("إجمالي العملاء", str(snapshot.total_customers)),
            ("العملاء النشطون", str(snapshot.active_customers)),
            ("العملاء الموقوفون", str(snapshot.suspended_customers)),
            ("التراخيص التجريبية", str(snapshot.trial_licenses)),
            ("التراخيص الشهرية", str(snapshot.monthly_licenses)),
            ("التراخيص السنوية", str(snapshot.yearly_licenses)),
            ("التراخيص الدائمة", str(snapshot.lifetime_licenses)),
            ("التراخيص المنتهية", str(snapshot.expired_licenses)),
            ("الشركات المتصلة", _optional_count(snapshot.online_companies)),
            ("الشركات غير المتصلة", _optional_count(snapshot.offline_companies)),
            ("الأجهزة المتصلة", _optional_count(snapshot.connected_devices)),
            ("مهام المزامنة المعلّقة", str(snapshot.pending_sync_count)),
            ("حالة خادم الحضور", _server_status_label(snapshot)),
            ("حالة قاعدة البيانات", _database_status_label(snapshot)),
            ("آخر إصدار منشور بنجاح", snapshot.latest_deployed_version or "لا يوجد"),
            ("تحديثات معلّقة", str(snapshot.pending_updates_count)),
            ("تحديثات فاشلة", str(snapshot.failed_updates_count)),
            ("تحديثات ناجحة", str(snapshot.successful_updates_count)),
            ("متوسط تقدّم التنزيل", _update_progress_label(snapshot.average_update_download_progress_percent)),
        )
        for index, (title, value) in enumerate(cards):
            row, column = divmod(index, _CARD_COLUMNS)
            self._grid.addWidget(_stat_tile(title, value), row, column)

        self.customer_growth_chart.set_data(snapshot.customer_growth)
        self.license_distribution_chart.set_data(snapshot.license_distribution)
        self.online_companies_chart.set_data(snapshot.online_companies, snapshot.offline_companies)
        self.sync_activity_chart.set_data(snapshot.sync_activity_by_status)
        self.expiration_timeline_chart.set_data(snapshot.expiration_timeline)

        _fill_list(
            self.registrations_list,
            _customer_registration_lines(snapshot.recent_customer_registrations),
            empty_text="لا توجد تسجيلات حديثة.",
        )
        _fill_list(
            self.issuances_list,
            _license_event_lines(snapshot.recent_license_issuances),
            empty_text="لا يوجد إصدار تراخيص حديث.",
        )
        _fill_list(
            self.renewals_list,
            _license_event_lines(snapshot.recent_license_renewals),
            empty_text="لا يوجد تجديد تراخيص حديث.",
        )
        _fill_list(
            self.synchronization_list,
            _sync_activity_lines(snapshot.recent_synchronization),
            empty_text="لا يوجد نشاط مزامنة حديث.",
        )
        _fill_list(
            self.server_events_list,
            _server_event_lines(snapshot.recent_server_events),
            empty_text="لا توجد أحداث خادم حديثة.",
        )
        _fill_list(
            self.authentication_events_list,
            _audit_log_lines(snapshot.recent_authentication_events),
            empty_text="لا توجد أحداث مصادقة حديثة.",
        )
        _fill_list(
            self.audit_log_list,
            _audit_log_lines(snapshot.recent_audit_log),
            empty_text="سجل التدقيق فارغ.",
        )

        _clear_layout(self.expirations_layout)
        if not snapshot.upcoming_expirations:
            self.expirations_layout.addWidget(QLabel("لا توجد تراخيص قاربت على الانتهاء.", self))
        else:
            for expiration in snapshot.upcoming_expirations:
                text = (
                    f"{expiration.customer_name} — {expiration.license_type_label} — "
                    f"{expiration.days_remaining} يوم متبقٍ (ينتهي في {expiration.expires_at.isoformat()})"
                )
                self.expirations_layout.addWidget(QLabel(text, self))

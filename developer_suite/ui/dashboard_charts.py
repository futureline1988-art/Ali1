"""Dashboard chart widgets: customer growth, subscription status, online
companies, synchronization activity, and subscription expiration timeline.

Every widget here only draws whatever list of already-aggregated
values :class:`~developer_suite.services.dashboard_service.DashboardSnapshot`
hands it (see that module's own docstring for where each list comes
from) — no chart here queries a service or repository directly, the
same "the page only lays widgets out" boundary
:mod:`developer_suite.ui.dashboard_page` already established. Built on
``PySide6.QtCharts``, the same charting toolkit the Attendance
Client's own dashboard (``ui/dashboard_page.py``) already uses — no
second charting dependency is introduced.
"""

from __future__ import annotations

from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QBarSet,
    QChart,
    QChartView,
    QLineSeries,
    QPieSeries,
    QValueAxis,
)
from PySide6.QtCore import QMarginsF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from developer_suite.services.dashboard_service import (
    CustomerGrowthPoint,
    ExpirationTimelineBucket,
    SubscriptionStatusEntry,
    SyncActivityBucket,
)

_MIN_CHART_HEIGHT = 220

_SYNC_STATUS_LABELS_AR = {
    "applied": "مطبّق",
    "conflict": "تعارض",
    "rejected": "مرفوض",
}


def _base_chart() -> QChart:
    chart = QChart()
    chart.setMargins(QMarginsF(4, 4, 4, 4).toMargins())
    return chart


class CustomerGrowthChart(QChartView):
    """Line chart of cumulative registered customers, by month."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        chart = _base_chart()
        chart.setTitle("نمو العملاء")
        chart.legend().setVisible(False)
        super().__init__(chart, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setMinimumHeight(_MIN_CHART_HEIGHT)

        self._series = QLineSeries()
        chart.addSeries(self._series)

        self._axis_x = QBarCategoryAxis()
        self._axis_y = QValueAxis()
        self._axis_y.setLabelFormat("%d")
        self._axis_y.setMinorTickCount(0)
        chart.addAxis(self._axis_x, Qt.AlignBottom)
        chart.addAxis(self._axis_y, Qt.AlignLeft)
        self._series.attachAxis(self._axis_x)
        self._series.attachAxis(self._axis_y)

    def set_data(self, points: list[CustomerGrowthPoint]) -> None:
        """Redraw the line from :attr:`~developer_suite.services.dashboard_service.DashboardSnapshot.customer_growth`."""
        self._series.clear()
        self._axis_x.clear()
        self._axis_x.append([point.month for point in points])

        max_value = 1
        for index, point in enumerate(points):
            self._series.append(index, point.cumulative_customers)
            max_value = max(max_value, point.cumulative_customers)
        self._axis_y.setRange(0, max_value + 1)


class SubscriptionStatusChart(QChartView):
    """Pie chart of subscriptions per effective status (active/suspended/expired)."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        chart = _base_chart()
        chart.setTitle("حالة الاشتراكات")
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignBottom)
        super().__init__(chart, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setMinimumHeight(_MIN_CHART_HEIGHT)

        self._series = QPieSeries()
        chart.addSeries(self._series)

    def set_data(self, entries: list[SubscriptionStatusEntry]) -> None:
        """Redraw the slices from :attr:`~developer_suite.services.dashboard_service.DashboardSnapshot.subscription_status_breakdown`."""
        self._series.clear()
        for entry in entries:
            if entry.count > 0:
                self._series.append(f"{entry.status_label} ({entry.count})", entry.count)


class OnlineCompaniesChart(QChartView):
    """Bar chart of currently online vs. offline companies."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        chart = _base_chart()
        chart.setTitle("حالة اتصال الشركات")
        chart.legend().setVisible(False)
        super().__init__(chart, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setMinimumHeight(_MIN_CHART_HEIGHT)

        self._bar_set = QBarSet("الشركات")
        self._series = QBarSeries()
        self._series.append(self._bar_set)
        chart.addSeries(self._series)

        self._axis_x = QBarCategoryAxis()
        self._axis_x.append(["متصلة", "غير متصلة"])
        self._axis_y = QValueAxis()
        self._axis_y.setLabelFormat("%d")
        self._axis_y.setMinorTickCount(0)
        chart.addAxis(self._axis_x, Qt.AlignBottom)
        chart.addAxis(self._axis_y, Qt.AlignLeft)
        self._series.attachAxis(self._axis_x)
        self._series.attachAxis(self._axis_y)

    def set_data(self, online: int | None, offline: int | None) -> None:
        """Redraw the bars from :attr:`~developer_suite.services.dashboard_service.DashboardSnapshot.online_companies`/``offline_companies``."""
        if self._bar_set.count():
            self._bar_set.remove(0, self._bar_set.count())
        online_value = online or 0
        offline_value = offline or 0
        self._bar_set.append(online_value)
        self._bar_set.append(offline_value)
        self._axis_y.setRange(0, max(online_value, offline_value, 1) + 1)


class SyncActivityChart(QChartView):
    """Bar chart of recent change-record counts, per outcome status."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        chart = _base_chart()
        chart.setTitle("نشاط المزامنة")
        chart.legend().setVisible(False)
        super().__init__(chart, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setMinimumHeight(_MIN_CHART_HEIGHT)

        self._bar_set = QBarSet("التغييرات")
        self._series = QBarSeries()
        self._series.append(self._bar_set)
        chart.addSeries(self._series)

        self._axis_x = QBarCategoryAxis()
        self._axis_y = QValueAxis()
        self._axis_y.setLabelFormat("%d")
        self._axis_y.setMinorTickCount(0)
        chart.addAxis(self._axis_x, Qt.AlignBottom)
        chart.addAxis(self._axis_y, Qt.AlignLeft)
        self._series.attachAxis(self._axis_x)
        self._series.attachAxis(self._axis_y)

    def set_data(self, buckets: list[SyncActivityBucket]) -> None:
        """Redraw the bars from :attr:`~developer_suite.services.dashboard_service.DashboardSnapshot.sync_activity_by_status`."""
        if self._bar_set.count():
            self._bar_set.remove(0, self._bar_set.count())
        self._axis_x.clear()

        categories = []
        max_value = 1
        for bucket in buckets:
            categories.append(_SYNC_STATUS_LABELS_AR.get(bucket.status_label, bucket.status_label))
            self._bar_set.append(bucket.count)
            max_value = max(max_value, bucket.count)
        self._axis_x.append(categories)
        self._axis_y.setRange(0, max_value + 1)


class ExpirationTimelineChart(QChartView):
    """Bar chart of active-subscription expirations, by upcoming month."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        chart = _base_chart()
        chart.setTitle("الجدول الزمني لانتهاء الاشتراكات")
        chart.legend().setVisible(False)
        super().__init__(chart, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setMinimumHeight(_MIN_CHART_HEIGHT)

        self._bar_set = QBarSet("اشتراكات منتهية")
        self._series = QBarSeries()
        self._series.append(self._bar_set)
        chart.addSeries(self._series)

        self._axis_x = QBarCategoryAxis()
        self._axis_y = QValueAxis()
        self._axis_y.setLabelFormat("%d")
        self._axis_y.setMinorTickCount(0)
        chart.addAxis(self._axis_x, Qt.AlignBottom)
        chart.addAxis(self._axis_y, Qt.AlignLeft)
        self._series.attachAxis(self._axis_x)
        self._series.attachAxis(self._axis_y)

    def set_data(self, buckets: list[ExpirationTimelineBucket]) -> None:
        """Redraw the bars from :attr:`~developer_suite.services.dashboard_service.DashboardSnapshot.expiration_timeline`."""
        if self._bar_set.count():
            self._bar_set.remove(0, self._bar_set.count())
        self._axis_x.clear()

        categories = [bucket.month for bucket in buckets]
        max_value = 1
        for bucket in buckets:
            self._bar_set.append(bucket.count)
            max_value = max(max_value, bucket.count)
        self._axis_x.append(categories)
        self._axis_y.setRange(0, max_value + 1)

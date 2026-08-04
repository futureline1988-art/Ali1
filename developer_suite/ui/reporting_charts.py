"""Generic "count per label" chart widgets for the Reporting & Analytics module.

Built on the exact same ``PySide6.QtCharts`` primitives and
``_base_chart()``/"widget only draws pre-aggregated data" pattern
:mod:`developer_suite.ui.dashboard_charts` already established — no
second charting dependency, no chart here ever queries a service or
repository directly. Unlike that module's five widgets (each bound to
one specific dashboard-snapshot dataclass), these two are generic over
:func:`~developer_suite.services.reporting_service.group_and_count`'s
``{"group": ..., "count": ...}`` row shape, since a Reporting page
report can be grouped by any column the user picks (device type,
update status, audit action, ...) — a fixed dataclass per chart would
mean a new chart class per grouping, defeating the point of a generic
report grouper.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QBarSet,
    QChart,
    QChartView,
    QPieSeries,
    QValueAxis,
)
from PySide6.QtCore import QMarginsF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

_MIN_CHART_HEIGHT = 220
#: Group buckets beyond this count are folded into a single "أخرى"
#: ("other") slice/bar — a report grouped by a high-cardinality column
#: (e.g. raw ids) would otherwise render an unreadable chart; the full
#: breakdown is still available in the report's own results table.
_MAX_DISTINCT_CATEGORIES = 10


def _base_chart(title: str) -> QChart:
    chart = QChart()
    chart.setMargins(QMarginsF(4, 4, 4, 4).toMargins())
    chart.setTitle(title)
    return chart


def _condense(rows: list[dict[str, Any]], *, max_categories: int) -> list[tuple[str, int]]:
    """Fold ``rows`` (already sorted most-frequent-first by :func:`group_and_count`) to at most ``max_categories`` entries."""
    pairs = [(str(row.get("group", "")), int(row.get("count", 0))) for row in rows]
    if len(pairs) <= max_categories:
        return pairs
    kept, overflow = pairs[: max_categories - 1], pairs[max_categories - 1 :]
    overflow_total = sum(count for _label, count in overflow)
    return [*kept, ("أخرى", overflow_total)]


class CategoryCountBarChart(QChartView):
    """Bar chart of an arbitrary column's value counts (see :func:`~developer_suite.services.reporting_service.group_and_count`)."""

    def __init__(self, *, title: str = "", parent: QWidget | None = None) -> None:
        chart = _base_chart(title)
        chart.legend().setVisible(False)
        super().__init__(chart, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setMinimumHeight(_MIN_CHART_HEIGHT)

        self._chart = chart
        self._bar_set = QBarSet("العدد")
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

    def set_title(self, title: str) -> None:
        """Change the chart's title (e.g. to reflect which column the report is currently grouped by)."""
        self._chart.setTitle(title)

    def set_data(self, rows: list[dict[str, Any]]) -> None:
        """Redraw the bars from a :func:`~developer_suite.services.reporting_service.group_and_count` result's ``rows``."""
        if self._bar_set.count():
            self._bar_set.remove(0, self._bar_set.count())
        self._axis_x.clear()

        categories: list[str] = []
        max_value = 1
        for label, count in _condense(rows, max_categories=_MAX_DISTINCT_CATEGORIES):
            categories.append(label)
            self._bar_set.append(count)
            max_value = max(max_value, count)
        self._axis_x.append(categories)
        self._axis_y.setRange(0, max_value + 1)


class CategoryCountPieChart(QChartView):
    """Pie chart of an arbitrary column's value counts (see :func:`~developer_suite.services.reporting_service.group_and_count`)."""

    def __init__(self, *, title: str = "", parent: QWidget | None = None) -> None:
        chart = _base_chart(title)
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignBottom)
        super().__init__(chart, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setMinimumHeight(_MIN_CHART_HEIGHT)

        self._chart = chart
        self._series = QPieSeries()
        chart.addSeries(self._series)

    def set_title(self, title: str) -> None:
        """Change the chart's title (e.g. to reflect which column the report is currently grouped by)."""
        self._chart.setTitle(title)

    def set_data(self, rows: list[dict[str, Any]]) -> None:
        """Redraw the slices from a :func:`~developer_suite.services.reporting_service.group_and_count` result's ``rows``."""
        self._series.clear()
        for label, count in _condense(rows, max_categories=_MAX_DISTINCT_CATEGORIES):
            if count > 0:
                self._series.append(f"{label} ({count})", count)

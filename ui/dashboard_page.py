"""Dashboard page: the home screen showing cross-module summary statistics."""

from __future__ import annotations

from typing import Any

from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QBarSet,
    QChart,
    QChartView,
    QLineSeries,
    QValueAxis,
)
from PySide6.QtCore import QMarginsF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from controllers.dashboard_controller import DashboardController
from ui.theme import Palette, get_theme_manager
from ui.widgets import Card, StatCard, make_heading_label, make_secondary_label, make_status_label

_AUTO_REFRESH_INTERVAL_MS = 60_000

# (trend key, series legend label, palette color attribute)
_TREND_SERIES_SPECS: list[tuple[str, str, str]] = [
    ("present", "حاضر", "success"),
    ("late", "متأخر", "warning"),
    ("absent", "غائب", "danger"),
    ("on_leave", "إجازة", "accent"),
]


class AttendanceTrendChart(QChartView):
    """Line chart of company-wide daily attendance status counts.

    Draws one series per :data:`_TREND_SERIES_SPECS` entry against a
    shared date-category x-axis, restyled on every
    :meth:`apply_palette` call so it follows light/dark theme switches
    the same way the rest of the UI does (Qt Charts is drawn with
    ``QPainter`` and is not reachable through the app's QSS
    stylesheet).
    """

    def __init__(self, *, parent: QWidget | None = None) -> None:
        """Build an empty trend chart, ready for :meth:`set_data`."""
        chart = QChart()
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignBottom)
        chart.setMargins(QMarginsF(4, 4, 4, 4).toMargins())
        super().__init__(chart, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setMinimumHeight(240)

        self._axis_x = QBarCategoryAxis()
        self._axis_y = QValueAxis()
        self._axis_y.setLabelFormat("%d")
        self._axis_y.setMinorTickCount(0)
        chart.addAxis(self._axis_x, Qt.AlignBottom)
        chart.addAxis(self._axis_y, Qt.AlignLeft)

        self._series: dict[str, QLineSeries] = {}
        for key, label, _color_attr in _TREND_SERIES_SPECS:
            series = QLineSeries()
            series.setName(label)
            chart.addSeries(series)
            series.attachAxis(self._axis_x)
            series.attachAxis(self._axis_y)
            self._series[key] = series

    def set_data(self, trend: list[dict[str, Any]]) -> None:
        """Redraw every series from a
        :meth:`~controllers.dashboard_controller.DashboardController.get_attendance_trend`
        result.

        Args:
            trend: One dict per day, each with a ``date`` (ISO format)
                key plus one integer count per attendance status.
        """
        categories = [row["date"][5:] for row in trend]
        self._axis_x.clear()
        self._axis_x.append(categories)

        max_value = 1
        for key, series in self._series.items():
            series.clear()
            for index, row in enumerate(trend):
                value = int(row.get(key, 0))
                series.append(index, value)
                max_value = max(max_value, value)
        self._axis_y.setRange(0, max_value + 1)

    def apply_palette(self, palette: Palette) -> None:
        """Recolor the chart's chrome and series to match ``palette``."""
        chart = self.chart()
        chart.setBackgroundBrush(QColor(palette.surface_bg))
        chart.setBackgroundPen(QColor(palette.border))
        chart.legend().setLabelColor(QColor(palette.text_primary))
        for axis in (self._axis_x, self._axis_y):
            axis.setLabelsColor(QColor(palette.text_secondary))
            axis.setLinePen(QColor(palette.border))
            axis.setGridLineColor(QColor(palette.border))

        for key, _label, color_attr in _TREND_SERIES_SPECS:
            pen = self._series[key].pen()
            pen.setColor(QColor(getattr(palette, color_attr)))
            pen.setWidth(2)
            self._series[key].setPen(pen)


class DepartmentBarChart(QChartView):
    """Bar chart of active headcount per department.

    Restyled on every :meth:`apply_palette` call, for the same reason
    as :class:`AttendanceTrendChart`.
    """

    def __init__(self, *, parent: QWidget | None = None) -> None:
        """Build an empty bar chart, ready for :meth:`set_data`."""
        chart = QChart()
        chart.legend().setVisible(False)
        chart.setMargins(QMarginsF(4, 4, 4, 4).toMargins())
        super().__init__(chart, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setMinimumHeight(240)

        self._bar_set = QBarSet("الموظفون")
        from PySide6.QtCharts import QBarSeries

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

    def set_data(self, rows: list[dict[str, Any]]) -> None:
        """Redraw the bars from a
        :meth:`~controllers.dashboard_controller.DashboardController.get_department_breakdown`
        result.

        Args:
            rows: One dict per department with ``name`` and
                ``employee_count`` keys.
        """
        if self._bar_set.count():
            self._bar_set.remove(0, self._bar_set.count())
        self._axis_x.clear()

        categories = []
        max_value = 1
        for row in rows:
            categories.append(str(row["name"]))
            count = int(row["employee_count"])
            self._bar_set.append(count)
            max_value = max(max_value, count)
        self._axis_x.append(categories)
        self._axis_y.setRange(0, max_value + 1)

    def apply_palette(self, palette: Palette) -> None:
        """Recolor the chart's chrome and bars to match ``palette``."""
        chart = self.chart()
        chart.setBackgroundBrush(QColor(palette.surface_bg))
        chart.setBackgroundPen(QColor(palette.border))
        for axis in (self._axis_x, self._axis_y):
            axis.setLabelsColor(QColor(palette.text_secondary))
            axis.setLinePen(QColor(palette.border))
            axis.setGridLineColor(QColor(palette.border))
        self._bar_set.setColor(QColor(palette.accent))


class DashboardPage(QWidget):
    """Renders :class:`~controllers.dashboard_controller.DashboardController`'s summary.

    Refreshes on show and every :data:`_AUTO_REFRESH_INTERVAL_MS`
    while visible, so the numbers stay current if the screen is left
    open in the background.
    """

    def __init__(
        self,
        *,
        company_id: int,
        current_user_id: int | None = None,
        permission_codes: frozenset[str] = frozenset(),
        parent: QWidget | None = None,
    ) -> None:
        """Create the dashboard page.

        Args:
            company_id: The company this dashboard reports on.
            current_user_id: The signed-in user, for audit attribution.
            permission_codes: The signed-in user's granted permission
                codes.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._controller = DashboardController(
            company_id=company_id,
            actor_user_id=current_user_id,
            permission_codes=permission_codes,
        )
        self._controller.operation_failed.connect(self._on_load_failed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)

        header_row = QHBoxLayout()
        header_row.addWidget(make_heading_label("لوحة التحكم"))
        header_row.addStretch(1)
        self._updated_label = make_secondary_label("")
        header_row.addWidget(self._updated_label)
        layout.addLayout(header_row)

        self._error_label = make_status_label("", "danger")
        self._error_label.hide()
        layout.addWidget(self._error_label)

        self._overview_grid = QGridLayout()
        self._overview_grid.setSpacing(16)
        layout.addLayout(self._overview_grid)

        attendance_heading = make_heading_label("حضور اليوم")
        layout.addWidget(attendance_heading)

        self._attendance_grid = QGridLayout()
        self._attendance_grid.setSpacing(16)
        layout.addLayout(self._attendance_grid)

        charts_heading = make_heading_label("إحصائيات تنفيذية")
        layout.addWidget(charts_heading)

        charts_row = QHBoxLayout()
        charts_row.setSpacing(16)
        layout.addLayout(charts_row)

        trend_card = Card(parent=self)
        trend_card.body_layout.addWidget(make_secondary_label("اتجاه الحضور (آخر 14 يومًا)"))
        self._trend_chart = AttendanceTrendChart(parent=trend_card)
        trend_card.body_layout.addWidget(self._trend_chart)
        charts_row.addWidget(trend_card, 3)

        department_card = Card(parent=self)
        department_card.body_layout.addWidget(make_secondary_label("الموظفون حسب القسم"))
        self._department_chart = DepartmentBarChart(parent=department_card)
        department_card.body_layout.addWidget(self._department_chart)
        charts_row.addWidget(department_card, 2)

        layout.addStretch(1)

        self._overview_cards: dict[str, StatCard] = {}
        self._attendance_cards: dict[str, StatCard] = {}
        self._build_overview_cards()
        self._build_attendance_cards()

        get_theme_manager().theme_changed.connect(self._apply_chart_theme)
        self._apply_chart_theme()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(_AUTO_REFRESH_INTERVAL_MS)
        self._refresh_timer.timeout.connect(self.refresh)

        self.refresh()

    # ------------------------------------------------------------------
    # Card construction
    # ------------------------------------------------------------------

    def _build_overview_cards(self) -> None:
        """Lay out the top row of headline metric cards."""
        specs = [
            ("active_employees", "الموظفون النشطون", None),
            ("departments", "الأقسام", None),
            ("devices_online", "الأجهزة المتصلة", "success"),
            ("devices_offline", "الأجهزة غير المتصلة", "warning"),
        ]
        for column, (key, title, status) in enumerate(specs):
            card = StatCard(title, "—", status=status)
            self._overview_cards[key] = card
            self._overview_grid.addWidget(card, 0, column)

    def _build_attendance_cards(self) -> None:
        """Lay out today's attendance breakdown cards."""
        specs = [
            ("present", "حاضر", "success"),
            ("late", "متأخر", "warning"),
            ("absent", "غائب", "danger"),
            ("on_leave", "إجازة", None),
            ("not_yet_computed", "لم يُحتسب بعد", None),
        ]
        for column, (key, title, status) in enumerate(specs):
            card = StatCard(title, "—", status=status)
            self._attendance_cards[key] = card
            self._attendance_grid.addWidget(card, 0, column)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Fetch the latest summary and charts data, and update every widget."""
        self._error_label.hide()
        summary = self._controller.get_summary()
        if summary is not None:
            self._apply_summary(summary)

        self._trend_chart.set_data(self._controller.get_attendance_trend())
        self._department_chart.set_data(self._controller.get_department_breakdown())

    def _apply_summary(self, summary: dict[str, Any]) -> None:
        """Update card values from a fetched summary dict.

        Args:
            summary: The dict returned by
                :meth:`~controllers.dashboard_controller.DashboardController.get_summary`.
        """
        active = summary["active_employee_count"]
        total = summary["total_employee_count"]
        self._overview_cards["active_employees"].set_value(str(active))
        self._overview_cards["active_employees"].set_subtitle(f"من أصل {total}")

        self._overview_cards["departments"].set_value(str(summary["department_count"]))

        device_total = summary["device_count"]
        self._overview_cards["devices_online"].set_value(str(summary["devices_online"]))
        self._overview_cards["devices_online"].set_subtitle(f"من أصل {device_total}")
        self._overview_cards["devices_offline"].set_value(str(summary["devices_offline"]))

        attendance_today = summary["attendance_today"]
        self._attendance_cards["present"].set_value(str(attendance_today["present"]))
        late_and_early = attendance_today["late"] + attendance_today["early_leave"]
        self._attendance_cards["late"].set_value(str(late_and_early))
        self._attendance_cards["absent"].set_value(str(attendance_today["absent"]))
        self._attendance_cards["on_leave"].set_value(str(attendance_today["on_leave"]))
        self._attendance_cards["not_yet_computed"].set_value(
            str(attendance_today["not_yet_computed"])
        )

        self._updated_label.setText(f"اليوم: {summary['today']}")

    def _on_load_failed(self, message: str) -> None:
        """Show an inline error banner when a summary fetch fails.

        Args:
            message: The underlying error text.
        """
        self._error_label.setText(f"تعذر تحميل بيانات لوحة التحكم: {message}")
        self._error_label.show()

    def _apply_chart_theme(self) -> None:
        """Recolor both charts from the currently active theme palette."""
        palette = get_theme_manager().current_palette
        self._trend_chart.apply_palette(palette)
        self._department_chart.apply_palette(palette)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Refresh immediately and (re)start the auto-refresh timer on show."""
        super().showEvent(event)
        self.refresh()
        self._refresh_timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Stop auto-refreshing while the page is not visible."""
        super().hideEvent(event)
        self._refresh_timer.stop()

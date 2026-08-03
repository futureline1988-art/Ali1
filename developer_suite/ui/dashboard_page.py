"""The Developer Dashboard: the Developer Suite's main landing page.

Talks only to :class:`~developer_suite.services.dashboard_service.DashboardService`
— never to any repository, or to the customer/license/sync services
directly — matching this platform's established service/UI boundary
(:class:`~developer_suite.ui.customer_management_page.CustomerManagementPage`'s
own docstring). Every number shown here is computed by that service;
this page only lays widgets out.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from developer_suite.services.dashboard_service import DashboardService, DashboardSnapshot

_TILE_COLUMNS = 4


def _stat_tile(title: str, value: str) -> QGroupBox:
    """Build one labeled statistic tile."""
    box = QGroupBox(title)
    layout = QVBoxLayout(box)
    label = QLabel(value)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    font = label.font()
    font.setPointSize(18)
    font.setBold(True)
    label.setFont(font)
    layout.addWidget(label)
    return box


def _optional_count(value: int | None) -> str:
    return str(value) if value is not None else "غير متاح"


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "لم تتم بعد"
    return value.strftime("%Y-%m-%d %H:%M")


def _server_status_label(snapshot: DashboardSnapshot) -> str:
    if snapshot.server_reachable is None:
        return "غير معروف"
    if not snapshot.server_reachable:
        return "غير متصل"
    return f"متصل (إصدار {snapshot.server_version or '؟'})"


def _clear_layout(layout) -> None:
    """Remove and dispose of every widget currently in ``layout``."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


class DashboardPage(QWidget):
    """The main dashboard: platform-wide counts and status at a glance."""

    def __init__(self, dashboard_service: DashboardService, *, parent: QWidget | None = None) -> None:
        """Build the page and load the initial snapshot.

        Args:
            dashboard_service: The service every displayed number comes from.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._service = dashboard_service

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel("لوحة التحكم", self)
        title_font = title.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        header.addWidget(title, stretch=1)

        self.refresh_button = QPushButton("تحديث", self)
        self.refresh_button.clicked.connect(self.reload)
        header.addWidget(self.refresh_button)
        outer.addLayout(header)

        self._grid = QGridLayout()
        self._grid.setSpacing(12)
        outer.addLayout(self._grid)

        self.expirations_box = QGroupBox("تراخيص قاربت على الانتهاء", self)
        self.expirations_layout = QVBoxLayout(self.expirations_box)
        outer.addWidget(self.expirations_box)

        outer.addStretch(1)

        self.reload()

    def reload(self) -> None:
        """Recompute the snapshot and repaint every tile."""
        self._populate(self._service.get_snapshot())

    def _populate(self, snapshot: DashboardSnapshot) -> None:
        """Fill the grid and expirations list from ``snapshot``, replacing current contents."""
        _clear_layout(self._grid)

        tiles = (
            ("إجمالي العملاء", str(snapshot.total_customers)),
            ("العملاء النشطون", str(snapshot.active_customers)),
            ("العملاء الموقوفون", str(snapshot.suspended_customers)),
            ("الشركات المتصلة", _optional_count(snapshot.online_companies)),
            ("الشركات غير المتصلة", _optional_count(snapshot.offline_companies)),
            ("التراخيص النشطة", str(snapshot.active_licenses)),
            ("التراخيص المنتهية", str(snapshot.expired_licenses)),
            ("التراخيص التجريبية", str(snapshot.trial_licenses)),
            ("آخر مزامنة ناجحة", _format_datetime(snapshot.last_sync_at)),
            ("تغييرات بانتظار المزامنة", str(snapshot.pending_sync_count)),
            ("حالة خادم الحضور", _server_status_label(snapshot)),
            ("إصدار المنصة", snapshot.platform_version),
        )
        for index, (title, value) in enumerate(tiles):
            row, column = divmod(index, _TILE_COLUMNS)
            self._grid.addWidget(_stat_tile(title, value), row, column)

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

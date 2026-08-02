"""Dashboard page: the home screen showing cross-module summary statistics."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from controllers.dashboard_controller import DashboardController
from ui.widgets import StatCard, make_heading_label, make_secondary_label, make_status_label

_AUTO_REFRESH_INTERVAL_MS = 60_000


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

        layout.addStretch(1)

        self._overview_cards: dict[str, StatCard] = {}
        self._attendance_cards: dict[str, StatCard] = {}
        self._build_overview_cards()
        self._build_attendance_cards()

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
        """Fetch the latest summary and update every card."""
        self._error_label.hide()
        summary = self._controller.get_summary()
        if summary is None:
            return
        self._apply_summary(summary)

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

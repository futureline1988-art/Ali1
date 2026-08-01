"""Main application window: sidebar navigation, top bar, and the page stack.

``MainWindow`` is a generic shell — it knows nothing about any specific
feature module. Pages (dashboard, employees, attendance, ...) are handed
to it via :meth:`MainWindow.register_page` by the composition root
(``main.py``), once every view has been constructed. This keeps this
file stable as new feature screens are added, and lets the app start up
successfully even before every screen exists yet (a page simply is not
in the sidebar until it is registered).
"""

from __future__ import annotations

from typing import Any

import shiboken6
from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from services.notification_service import Notification, get_notification_center
from ui.theme import get_theme_manager
from ui.widgets import Sidebar, ToastNotification, TopBar
from utils.security import SessionManager

_SESSION_CHECK_INTERVAL_MS = 60_000
_TOAST_MARGIN = 16
_TOAST_SPACING = 12


class MainWindow(QMainWindow):
    """The authenticated application shell.

    Attributes:
        company_id: The company this window's session is scoped to.
        current_user: The signed-in user's data, as returned by
            :meth:`~controllers.auth_controller.AuthController.login`.
    """

    logout_requested = Signal()
    """Emitted when the user explicitly clicks "logout"."""

    session_expired = Signal()
    """Emitted when :attr:`session_manager` reports the session has gone idle."""

    def __init__(
        self,
        *,
        company_id: int,
        current_user: dict[str, Any],
        session_manager: SessionManager,
        parent: QWidget | None = None,
    ) -> None:
        """Build the main window shell (sidebar, top bar, empty page stack).

        Args:
            company_id: The company this window's session is scoped to.
            current_user: The signed-in user's data (from
                :meth:`~controllers.auth_controller.AuthController.login`).
            session_manager: The active desktop session, polled for
                idle-timeout and touched on user activity.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.company_id = company_id
        self.current_user = current_user
        self.session_manager = session_manager

        self.setWindowTitle("نظام إدارة الحضور والانصراف")
        self.resize(1440, 900)

        self._pages: dict[str, QWidget] = {}
        self._page_titles: dict[str, str] = {}
        self._active_toasts: list[ToastNotification] = []

        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.sidebar = Sidebar([], title="نظام الحضور", parent=central)
        self.sidebar.setFixedWidth(240)
        self.sidebar.navigate_requested.connect(self.show_page)
        root_layout.addWidget(self.sidebar)

        content_column = QWidget(central)
        content_layout = QVBoxLayout(content_column)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        root_layout.addWidget(content_column, stretch=1)

        self.top_bar = TopBar(parent=content_column)
        self.top_bar.theme_toggle_requested.connect(get_theme_manager().toggle_theme)
        self.top_bar.logout_requested.connect(self._on_logout_clicked)
        content_layout.addWidget(self.top_bar)

        self.page_stack = QStackedWidget(content_column)
        content_layout.addWidget(self.page_stack, stretch=1)

        self._apply_user_label()

        get_notification_center().notification_posted.connect(self._on_notification_posted)

        self._session_timer = QTimer(self)
        self._session_timer.setInterval(_SESSION_CHECK_INTERVAL_MS)
        self._session_timer.timeout.connect(self._check_session_alive)
        self._session_timer.start()

        app_instance = QApplication.instance()
        if app_instance is not None:
            app_instance.installEventFilter(self)

    # ------------------------------------------------------------------
    # Page registration / navigation
    # ------------------------------------------------------------------

    def register_page(self, route: str, label: str, widget: QWidget) -> None:
        """Add a feature screen to the sidebar and page stack.

        Args:
            route: A unique key identifying this page (e.g.
                ``"dashboard"``, ``"employees"``).
            label: The sidebar's display label for this route.
            widget: The page widget itself.

        Raises:
            ValueError: If ``route`` was already registered.
        """
        if route in self._pages:
            raise ValueError(f"Route {route!r} is already registered.")

        self._pages[route] = widget
        self._page_titles[route] = label
        self.page_stack.addWidget(widget)
        self.sidebar.add_item(route, label)

        if self.page_stack.count() == 1:
            self.show_page(route)

    def show_page(self, route: str) -> None:
        """Switch the visible page to ``route``.

        Args:
            route: A previously :meth:`register_page`-ed route key; a
                no-op if unknown.
        """
        widget = self._pages.get(route)
        if widget is None:
            return
        self.page_stack.setCurrentWidget(widget)
        self.sidebar.set_active_route(route)
        self.top_bar.set_page_title(self._page_titles[route])

    # ------------------------------------------------------------------
    # Top bar / session
    # ------------------------------------------------------------------

    def _apply_user_label(self) -> None:
        """Render the signed-in user's name and role in the top bar."""
        full_name = self.current_user.get("full_name", "")
        role_name = self.current_user.get("role_name", "")
        label = f"{full_name} - {role_name}" if role_name else full_name
        self.top_bar.set_user_label(label)

    def _on_logout_clicked(self) -> None:
        """Relay the top bar's logout click as :attr:`logout_requested`."""
        self.logout_requested.emit()

    def _check_session_alive(self) -> None:
        """Poll :attr:`session_manager`, emitting :attr:`session_expired` if idle."""
        if not self.session_manager.is_active:
            self._session_timer.stop()
            self.session_expired.emit()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """Touch the session on any user-input activity while this window is open.

        Args:
            watched: The object the event occurred on.
            event: The event itself.

        Returns:
            ``False`` always — this filter only observes, never
            consumes, events.
        """
        if event.type() in (
            QEvent.MouseButtonPress,
            QEvent.KeyPress,
            QEvent.Wheel,
        ):
            self.session_manager.touch()
        return False

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def _on_notification_posted(self, notification: Notification) -> None:
        """Show a toast for a notification relevant to this window's company.

        Args:
            notification: The posted notification; ignored if it
                belongs to a different company.
        """
        if notification.company_id not in (None, self.company_id):
            return

        toast = ToastNotification(notification, parent=self)
        toast.destroyed.connect(lambda: self._on_toast_closed(toast))
        self._active_toasts.append(toast)
        self._reposition_toasts()
        toast.show()

    def _on_toast_closed(self, toast: ToastNotification) -> None:
        """Drop a closed toast from the active list and reflow the rest.

        A toast's ``destroyed`` signal can still fire after this
        window's own underlying C++ object has been torn down (e.g.
        during application shutdown, when Qt destroys child widgets in
        an order this code does not control) — ``shiboken6.isValid``
        guards against touching ``self`` in that case.
        """
        if not shiboken6.isValid(self):
            return
        if toast in self._active_toasts:
            self._active_toasts.remove(toast)
        self._reposition_toasts()

    def _reposition_toasts(self) -> None:
        """Stack active toasts vertically from the window's top-right corner."""
        top_right = self.geometry().topRight()
        y = top_right.y() + _TOAST_MARGIN
        for toast in self._active_toasts:
            x = top_right.x() - toast.width() - _TOAST_MARGIN
            toast.move(x, y)
            y += toast.height() + _TOAST_SPACING

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Stop background timers and remove the global event filter on close."""
        self._session_timer.stop()
        app_instance = QApplication.instance()
        if app_instance is not None:
            app_instance.removeEventFilter(self)
        super().closeEvent(event)

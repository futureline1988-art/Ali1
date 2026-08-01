"""Shared, reusable widgets used across every screen in the desktop UI.

Every widget here is presentation-only: it takes plain data (strings,
numbers, a :class:`~services.notification_service.Notification`) and
emits Qt signals for user actions — none of them import a controller,
service, or repository directly.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QSplashScreen,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from services.notification_service import Notification, NotificationLevel

_LEVEL_STATUS = {
    NotificationLevel.INFO: "info",
    NotificationLevel.SUCCESS: "success",
    NotificationLevel.WARNING: "warning",
    NotificationLevel.ERROR: "danger",
}

_LEVEL_SYMBOL = {
    NotificationLevel.INFO: "ℹ",
    NotificationLevel.SUCCESS: "✓",
    NotificationLevel.WARNING: "⚠",
    NotificationLevel.ERROR: "✕",
}


def make_primary_button(text: str, *, parent: QWidget | None = None) -> QPushButton:
    """Create a filled accent-colored button (the "call to action" style).

    Args:
        text: The button's label.
        parent: Optional parent widget.

    Returns:
        A :class:`~PySide6.QtWidgets.QPushButton` styled via the
        ``variant="primary"`` QSS rule in :mod:`ui.theme`.
    """
    button = QPushButton(text, parent)
    button.setProperty("variant", "primary")
    button.setCursor(Qt.PointingHandCursor)
    return button


def make_danger_button(text: str, *, parent: QWidget | None = None) -> QPushButton:
    """Create a filled destructive-action button (delete, deactivate, etc.).

    Args:
        text: The button's label.
        parent: Optional parent widget.

    Returns:
        A :class:`~PySide6.QtWidgets.QPushButton` styled via the
        ``variant="danger"`` QSS rule in :mod:`ui.theme`.
    """
    button = QPushButton(text, parent)
    button.setProperty("variant", "danger")
    button.setCursor(Qt.PointingHandCursor)
    return button


def make_secondary_label(text: str, *, parent: QWidget | None = None) -> QLabel:
    """Create a muted, lower-emphasis text label.

    Args:
        text: The label's text.
        parent: Optional parent widget.

    Returns:
        A :class:`~PySide6.QtWidgets.QLabel` styled via the
        ``secondary="true"`` QSS rule in :mod:`ui.theme`.
    """
    label = QLabel(text, parent)
    label.setProperty("secondary", "true")
    return label


def make_heading_label(text: str, *, parent: QWidget | None = None) -> QLabel:
    """Create a large, bold heading label.

    Args:
        text: The heading's text.
        parent: Optional parent widget.

    Returns:
        A :class:`~PySide6.QtWidgets.QLabel` styled via the
        ``heading="true"`` QSS rule in :mod:`ui.theme`.
    """
    label = QLabel(text, parent)
    label.setProperty("heading", "true")
    return label


def make_status_label(text: str, status: str, *, parent: QWidget | None = None) -> QLabel:
    """Create a colored status-pill label (``"success"``, ``"warning"``, ``"danger"``).

    Args:
        text: The label's text.
        status: One of ``"success"``, ``"warning"``, ``"danger"`` —
            matched against the QSS rules in :mod:`ui.theme`. Any other
            value simply renders with the default text color.
        parent: Optional parent widget.

    Returns:
        A styled :class:`~PySide6.QtWidgets.QLabel`.
    """
    label = QLabel(text, parent)
    label.setProperty("status", status)
    return label


class Divider(QFrame):
    """A thin horizontal rule, styled via :mod:`ui.theme`'s ``#Divider`` rule."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        """Create a horizontal divider line."""
        super().__init__(parent)
        self.setObjectName("Divider")
        self.setFrameShape(QFrame.HLine)
        self.setFixedHeight(1)


class Card(QFrame):
    """A rounded, bordered container — this app's basic "surface" widget.

    Used as the building block for dashboard stat tiles, form panels,
    and list-detail views. Subclasses or callers add their own layout
    to :attr:`body_layout`.
    """

    def __init__(self, *, parent: QWidget | None = None) -> None:
        """Create an empty card with 20px internal padding."""
        super().__init__(parent)
        self.setObjectName("Card")
        self.body_layout = QVBoxLayout(self)
        self.body_layout.setContentsMargins(20, 18, 20, 18)
        self.body_layout.setSpacing(8)


class StatCard(Card):
    """A dashboard tile showing one headline number with a title/subtitle.

    Example:
        >>> card = StatCard("الموظفون النشطون", "128")
        >>> card.set_value("130")
    """

    clicked = Signal()
    """Emitted when the card is clicked (e.g. to navigate to the detail screen)."""

    def __init__(
        self,
        title: str,
        value: str,
        *,
        subtitle: str | None = None,
        status: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Create a stat card.

        Args:
            title: The metric's label (e.g. ``"الموظفون النشطون"``).
            value: The metric's headline value, pre-formatted (e.g.
                ``"128"``).
            subtitle: Optional secondary line (e.g. a trend or count
                breakdown).
            status: Optional status color for the value — see
                :func:`make_status_label`.
            parent: Optional parent widget.
        """
        super().__init__(parent=parent)
        self.setCursor(Qt.PointingHandCursor)

        self._title_label = make_secondary_label(title)
        self.body_layout.addWidget(self._title_label)

        if status is not None:
            self._value_label = make_status_label(value, status)
        else:
            self._value_label = QLabel(value)
        self._value_label.setProperty("heading", "true")
        self.body_layout.addWidget(self._value_label)

        self._subtitle_label: QLabel | None = None
        if subtitle is not None:
            self._subtitle_label = make_secondary_label(subtitle)
            self.body_layout.addWidget(self._subtitle_label)

    def set_value(self, value: str) -> None:
        """Update the headline value.

        Args:
            value: The new pre-formatted value.
        """
        self._value_label.setText(value)

    def set_subtitle(self, subtitle: str) -> None:
        """Update (or create) the subtitle line.

        Args:
            subtitle: The new subtitle text.
        """
        if self._subtitle_label is None:
            self._subtitle_label = make_secondary_label(subtitle)
            self.body_layout.addWidget(self._subtitle_label)
        else:
            self._subtitle_label.setText(subtitle)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Emit :attr:`clicked` on a left-button press, then defer to Qt."""
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class SidebarNavButton(QPushButton):
    """A single, checkable navigation entry in :class:`Sidebar`."""

    def __init__(self, route: str, text: str, *, parent: QWidget | None = None) -> None:
        """Create a sidebar nav button.

        Args:
            route: The route key this button navigates to (e.g.
                ``"dashboard"``), carried as data for the owning
                :class:`Sidebar` to read back on click.
            text: The visible label.
            parent: Optional parent widget.
        """
        super().__init__(text, parent)
        self.setObjectName("SidebarNavButton")
        self.route = route
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(44)
        self.setLayoutDirection(Qt.RightToLeft if _is_rtl_text(text) else Qt.LeftToRight)


def _is_rtl_text(text: str) -> bool:
    """Heuristic: whether ``text`` contains an Arabic character."""
    return any("؀" <= char <= "ۿ" for char in text)


class Sidebar(QWidget):
    """The application's primary navigation rail.

    Emits :attr:`navigate_requested` with a route key whenever the user
    clicks a nav entry; the owning main window is responsible for
    actually switching the displayed page (this widget has no
    knowledge of the page stack).
    """

    navigate_requested = Signal(str)
    """Emitted with the clicked entry's route key."""

    def __init__(
        self,
        items: list[tuple[str, str]],
        *,
        title: str = "",
        parent: QWidget | None = None,
    ) -> None:
        """Create a sidebar with the given navigation entries.

        Args:
            items: ``(route, label)`` pairs, in display order.
            title: Optional title/company-name shown above the entries.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setObjectName("Sidebar")

        self._nav_layout = QVBoxLayout(self)
        self._nav_layout.setContentsMargins(12, 20, 12, 20)
        self._nav_layout.setSpacing(4)

        if title:
            title_label = QLabel(title, self)
            title_label.setProperty("heading", "true")
            title_label.setWordWrap(True)
            self._nav_layout.addWidget(title_label)
            self._nav_layout.addSpacing(12)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, SidebarNavButton] = {}
        self._button_count = 0

        self._nav_layout.addItem(
            QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding)
        )
        self._first_button_index = self._nav_layout.count() - 1

        for route, label in items:
            self.add_item(route, label)

        if items:
            self._buttons[items[0][0]].setChecked(True)

    def add_item(self, route: str, label: str) -> None:
        """Append a new navigation entry.

        Args:
            route: The new entry's unique route key.
            label: The new entry's display label.

        Raises:
            ValueError: If ``route`` is already present.
        """
        if route in self._buttons:
            raise ValueError(f"Route {route!r} is already in this sidebar.")

        button = SidebarNavButton(route, label, parent=self)
        self._group.addButton(button)
        self._buttons[route] = button
        button.clicked.connect(lambda _checked, r=route: self.navigate_requested.emit(r))
        self._nav_layout.insertWidget(self._first_button_index + self._button_count, button)
        self._button_count += 1

    def set_active_route(self, route: str) -> None:
        """Mark ``route``'s button as the checked/active entry.

        Args:
            route: The route key to activate; a no-op if unknown.
        """
        button = self._buttons.get(route)
        if button is not None:
            button.setChecked(True)


class TopBar(QWidget):
    """The header bar shown above the active page: title, search, user menu."""

    theme_toggle_requested = Signal()
    """Emitted when the user clicks the light/dark theme toggle button."""

    logout_requested = Signal()
    """Emitted when the user clicks "logout"."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        """Create an empty top bar; call :meth:`set_page_title`/:meth:`set_user_label`."""
        super().__init__(parent)
        self.setObjectName("TopBar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(12)

        self._title_label = make_heading_label("")
        layout.addWidget(self._title_label)
        layout.addStretch(1)

        self._user_label = make_secondary_label("")
        layout.addWidget(self._user_label)

        self._theme_button = QToolButton(self)
        self._theme_button.setText("☽")  # crescent-moon glyph
        self._theme_button.setToolTip("تبديل المظهر")
        self._theme_button.setCursor(Qt.PointingHandCursor)
        self._theme_button.clicked.connect(self.theme_toggle_requested.emit)
        layout.addWidget(self._theme_button)

        self._logout_button = QToolButton(self)
        self._logout_button.setText("⏻")  # power-symbol glyph
        self._logout_button.setToolTip("تسجيل الخروج")
        self._logout_button.setCursor(Qt.PointingHandCursor)
        self._logout_button.clicked.connect(self.logout_requested.emit)
        layout.addWidget(self._logout_button)

    def set_page_title(self, title: str) -> None:
        """Set the current page's title.

        Args:
            title: The page title to display.
        """
        self._title_label.setText(title)

    def set_user_label(self, text: str) -> None:
        """Set the signed-in user summary text (e.g. name + role).

        Args:
            text: The text to display.
        """
        self._user_label.setText(text)


class ToastNotification(QFrame):
    """A transient, self-dismissing popup showing one notification.

    Positioned by the caller (typically anchored to a corner of the
    main window); closes itself automatically after ``duration_ms``.
    """

    def __init__(
        self,
        notification: Notification,
        *,
        duration_ms: int = 4000,
        parent: QWidget | None = None,
    ) -> None:
        """Create and show a toast for ``notification``.

        Args:
            notification: The notification to render.
            duration_ms: How long to stay visible before auto-closing.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setObjectName("Card")
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedWidth(340)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        status = _LEVEL_STATUS[notification.level]
        symbol = _LEVEL_SYMBOL[notification.level]
        header = make_status_label(f"{symbol}  {notification.title}", status)
        layout.addWidget(header)

        body = QLabel(notification.message, self)
        body.setWordWrap(True)
        layout.addWidget(body)

        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._fade_animation = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_animation.setDuration(300)
        self._fade_animation.setStartValue(0.0)
        self._fade_animation.setEndValue(1.0)
        self._fade_animation.setEasingCurve(QEasingCurve.OutCubic)

        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.timeout.connect(self.close)
        self._close_timer.start(duration_ms)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Start the fade-in animation whenever the toast becomes visible."""
        super().showEvent(event)
        self._fade_animation.start()


class LoadingOverlay(QWidget):
    """A semi-transparent overlay with a message, shown during long operations.

    Sized and positioned by the caller to cover the widget it blocks
    (typically ``overlay.setGeometry(parent.rect())`` on show).
    """

    def __init__(self, message: str = "جارٍ التحميل...", *, parent: QWidget | None = None) -> None:
        """Create a hidden loading overlay; call :meth:`show` to display it.

        Args:
            message: The text shown beneath the progress indicator.
            parent: The widget this overlay should cover.
        """
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background-color: rgba(0, 0, 0, 0.35);")

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)

        progress = QProgressBar(self)
        progress.setRange(0, 0)
        progress.setFixedWidth(220)
        layout.addWidget(progress, alignment=Qt.AlignCenter)

        label = QLabel(message, self)
        label.setStyleSheet("color: white; font-weight: 600;")
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)

    def set_message(self, message: str) -> None:
        """Update the overlay's message text.

        Args:
            message: The new message text.
        """
        for child in self.findChildren(QLabel):
            child.setText(message)
            break


class ConfirmDialog(QDialog):
    """A modal Yes/No confirmation dialog, for destructive or irreversible actions."""

    def __init__(
        self,
        title: str,
        message: str,
        *,
        danger: bool = False,
        confirm_text: str = "تأكيد",
        cancel_text: str = "إلغاء",
        parent: QWidget | None = None,
    ) -> None:
        """Create a confirmation dialog.

        Args:
            title: The dialog window's title.
            message: The confirmation question/explanation.
            danger: Whether the confirm button should use the
                destructive (red) style — for delete/deactivate actions.
            confirm_text: Label for the confirm button.
            cancel_text: Label for the cancel button.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)

        message_label = QLabel(message, self)
        message_label.setWordWrap(True)
        layout.addWidget(message_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)

        cancel_button = QPushButton(cancel_text, self)
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)

        confirm_button = (
            make_danger_button(confirm_text, parent=self)
            if danger
            else make_primary_button(confirm_text, parent=self)
        )
        confirm_button.clicked.connect(self.accept)
        confirm_button.setDefault(True)
        button_row.addWidget(confirm_button)

        layout.addLayout(button_row)

    @staticmethod
    def confirm(
        parent: QWidget | None,
        title: str,
        message: str,
        *,
        danger: bool = False,
    ) -> bool:
        """Show a confirmation dialog and block until answered.

        Args:
            parent: The dialog's parent widget.
            title: The dialog window's title.
            message: The confirmation question/explanation.
            danger: Whether the confirm button should use the
                destructive (red) style.

        Returns:
            ``True`` if the user confirmed, ``False`` otherwise.
        """
        dialog = ConfirmDialog(title, message, danger=danger, parent=parent)
        return dialog.exec() == QDialog.Accepted


class SearchBox(QLineEdit):
    """A search input with placeholder text and a debounced text-change signal."""

    search_changed = Signal(str)
    """Emitted with the current text after :attr:`debounce_ms` of inactivity."""

    def __init__(
        self,
        placeholder: str = "بحث...",
        *,
        debounce_ms: int = 300,
        parent: QWidget | None = None,
    ) -> None:
        """Create a debounced search box.

        Args:
            placeholder: Placeholder text shown when empty.
            debounce_ms: How long to wait after the last keystroke
                before emitting :attr:`search_changed`.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(debounce_ms)
        self._debounce_timer.timeout.connect(self._emit_search_changed)

        self.textChanged.connect(lambda _text: self._debounce_timer.start())

    def _emit_search_changed(self) -> None:
        self.search_changed.emit(self.text())


def build_splash_screen(*, logo_path: str | None = None, app_name: str = "") -> QSplashScreen:
    """Build the startup splash screen shown while the app initializes.

    Args:
        logo_path: Path to a logo image file; falls back to a plain
            colored pixmap if missing or not provided.
        app_name: Application name drawn on the splash if no logo is
            available.

    Returns:
        A ready-to-show :class:`~PySide6.QtWidgets.QSplashScreen`.
    """
    pixmap = QPixmap(480, 300)
    loaded = False
    if logo_path:
        candidate = QPixmap(logo_path)
        if not candidate.isNull():
            pixmap = candidate.scaled(
                480, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            loaded = True
    if not loaded:
        pixmap.fill(Qt.white)

    splash = QSplashScreen(pixmap)
    if not loaded and app_name:
        splash.showMessage(
            app_name,
            Qt.AlignHCenter | Qt.AlignVCenter,
            Qt.black,
        )
    return splash

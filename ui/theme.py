"""Fluent-Design-inspired visual theme: color palettes, QSS, and runtime switching.

Two color palettes (:data:`_LIGHT_PALETTE`, :data:`_DARK_PALETTE`) drive a
single QSS template (:func:`build_stylesheet`) covering every widget class
used across this application's screens. :class:`ThemeManager` owns which
palette is active for the running process and applies it to the whole
``QApplication`` at once, matching how :class:`~utils.i18n.LocaleManager`
owns the active language.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from config import Theme, get_config


@dataclass(frozen=True)
class Palette:
    """A named set of colors a QSS template is rendered against.

    Attributes:
        window_bg: The main content area's background.
        surface_bg: A raised surface's background (cards, panels, dialogs).
        surface_bg_hover: A raised surface's background on hover.
        sidebar_bg: The navigation sidebar's background.
        sidebar_bg_hover: A sidebar item's background on hover.
        sidebar_bg_selected: A sidebar item's background when active.
        border: The default subtle border color.
        border_strong: A more visible border (focused inputs, dividers).
        text_primary: Primary (highest-contrast) text color.
        text_secondary: Secondary (muted) text color.
        text_on_accent: Text color used on top of :attr:`accent`.
        accent: The brand accent color (primary buttons, links, focus ring).
        accent_hover: :attr:`accent` on hover.
        accent_pressed: :attr:`accent` while pressed.
        success: Positive/confirmation state color.
        warning: Caution state color.
        danger: Destructive/error state color.
        danger_hover: :attr:`danger` on hover.
        scrollbar_handle: The scrollbar thumb color.
        shadow: A translucent color used for card drop shadows (as an
            ``rgba()`` fragment, since Qt CSS has no real box-shadow).
    """

    window_bg: str
    surface_bg: str
    surface_bg_hover: str
    sidebar_bg: str
    sidebar_bg_hover: str
    sidebar_bg_selected: str
    border: str
    border_strong: str
    text_primary: str
    text_secondary: str
    text_on_accent: str
    accent: str
    accent_hover: str
    accent_pressed: str
    success: str
    warning: str
    danger: str
    danger_hover: str
    scrollbar_handle: str


_LIGHT_PALETTE = Palette(
    window_bg="#F5F6F8",
    surface_bg="#FFFFFF",
    surface_bg_hover="#F3F4F6",
    sidebar_bg="#1F2A44",
    sidebar_bg_hover="#2A3757",
    sidebar_bg_selected="#0F6CBD",
    border="#E1E3E8",
    border_strong="#C7CBD3",
    text_primary="#1B1F27",
    text_secondary="#5C6270",
    text_on_accent="#FFFFFF",
    accent="#0F6CBD",
    accent_hover="#0D5AA0",
    accent_pressed="#0A4A84",
    success="#107C41",
    warning="#C77F00",
    danger="#D13438",
    danger_hover="#B32A2E",
    scrollbar_handle="#C7CBD3",
)

_DARK_PALETTE = Palette(
    window_bg="#171A21",
    surface_bg="#20242E",
    surface_bg_hover="#2A2F3B",
    sidebar_bg="#12151C",
    sidebar_bg_hover="#1E222C",
    sidebar_bg_selected="#2B88D8",
    border="#2E323D",
    border_strong="#3E4350",
    text_primary="#F0F1F4",
    text_secondary="#A5AAB6",
    text_on_accent="#FFFFFF",
    accent="#2B88D8",
    accent_hover="#4B9CE2",
    accent_pressed="#1F6FB5",
    success="#3FB950",
    warning="#D8A73D",
    danger="#E5484D",
    danger_hover="#F0666A",
    scrollbar_handle="#3E4350",
)

_PALETTES: dict[Theme, Palette] = {
    Theme.LIGHT: _LIGHT_PALETTE,
    Theme.DARK: _DARK_PALETTE,
}

_QSS_TEMPLATE = """
* {{
    font-family: "{font_family}";
    font-size: {font_size}pt;
    outline: none;
}}

QWidget {{
    background-color: {window_bg};
    color: {text_primary};
}}

QMainWindow, QDialog {{
    background-color: {window_bg};
}}

/* --- Sidebar navigation --- */

#Sidebar {{
    background-color: {sidebar_bg};
    border: none;
}}

#Sidebar QLabel {{
    color: {text_on_accent};
}}

#SidebarNavButton {{
    background-color: transparent;
    color: #C9CEDA;
    text-align: left;
    padding: 10px 16px;
    border: none;
    border-radius: 8px;
    font-size: {font_size_lg}pt;
}}

#SidebarNavButton:hover {{
    background-color: {sidebar_bg_hover};
    color: {text_on_accent};
}}

#SidebarNavButton:checked {{
    background-color: {sidebar_bg_selected};
    color: {text_on_accent};
    font-weight: 600;
}}

/* --- Cards / surfaces --- */

#Card, .Card {{
    background-color: {surface_bg};
    border: 1px solid {border};
    border-radius: 12px;
}}

#Card:hover, .Card:hover {{
    border: 1px solid {border_strong};
}}

QFrame#Divider {{
    background-color: {border};
    max-height: 1px;
    min-height: 1px;
}}

/* --- Labels --- */

QLabel {{
    background: transparent;
    color: {text_primary};
}}

QLabel[secondary="true"] {{
    color: {text_secondary};
}}

QLabel[heading="true"] {{
    font-size: {font_size_xl}pt;
    font-weight: 700;
}}

/* --- Buttons --- */

QPushButton {{
    background-color: {surface_bg};
    color: {text_primary};
    border: 1px solid {border_strong};
    border-radius: 8px;
    padding: 8px 18px;
}}

QPushButton:hover {{
    background-color: {surface_bg_hover};
}}

QPushButton:pressed {{
    background-color: {border};
}}

QPushButton:disabled {{
    color: {text_secondary};
    border-color: {border};
}}

QPushButton[variant="primary"] {{
    background-color: {accent};
    color: {text_on_accent};
    border: none;
    font-weight: 600;
}}

QPushButton[variant="primary"]:hover {{
    background-color: {accent_hover};
}}

QPushButton[variant="primary"]:pressed {{
    background-color: {accent_pressed};
}}

QPushButton[variant="danger"] {{
    background-color: {danger};
    color: {text_on_accent};
    border: none;
    font-weight: 600;
}}

QPushButton[variant="danger"]:hover {{
    background-color: {danger_hover};
}}

QToolButton {{
    background-color: transparent;
    border: none;
    border-radius: 8px;
    padding: 6px;
}}

QToolButton:hover {{
    background-color: {surface_bg_hover};
}}

/* --- Inputs --- */

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QTextEdit, QPlainTextEdit {{
    background-color: {surface_bg};
    color: {text_primary};
    border: 1px solid {border_strong};
    border-radius: 8px;
    padding: 6px 10px;
    selection-background-color: {accent};
    selection-color: {text_on_accent};
}}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QDateEdit:focus, QTimeEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {accent};
}}

QLineEdit:disabled, QComboBox:disabled {{
    color: {text_secondary};
    background-color: {window_bg};
}}

QComboBox::drop-down {{
    border: none;
    width: 24px;
}}

QCheckBox, QRadioButton {{
    spacing: 8px;
    color: {text_primary};
}}

/* --- Tables / trees --- */

QTableView, QTreeView, QListView {{
    background-color: {surface_bg};
    alternate-background-color: {window_bg};
    border: 1px solid {border};
    border-radius: 10px;
    gridline-color: {border};
    selection-background-color: {accent};
    selection-color: {text_on_accent};
}}

QHeaderView::section {{
    background-color: {surface_bg};
    color: {text_secondary};
    border: none;
    border-bottom: 1px solid {border};
    padding: 8px;
    font-weight: 600;
}}

QTableView::item, QTreeView::item {{
    padding: 6px;
}}

/* --- Tabs --- */

QTabWidget::pane {{
    border: 1px solid {border};
    border-radius: 10px;
    top: -1px;
}}

QTabBar::tab {{
    background: transparent;
    color: {text_secondary};
    padding: 8px 18px;
    border: none;
}}

QTabBar::tab:selected {{
    color: {accent};
    font-weight: 600;
    border-bottom: 2px solid {accent};
}}

/* --- Scrollbars --- */

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {scrollbar_handle};
    border-radius: 5px;
    min-height: 24px;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}

QScrollBar::handle:horizontal {{
    background: {scrollbar_handle};
    border-radius: 5px;
    min-width: 24px;
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}

/* --- Status pills (dynamic property "status") --- */

QLabel[status="success"] {{
    color: {success};
    font-weight: 600;
}}

QLabel[status="warning"] {{
    color: {warning};
    font-weight: 600;
}}

QLabel[status="danger"] {{
    color: {danger};
    font-weight: 600;
}}

/* --- Progress / toast --- */

QProgressBar {{
    background-color: {window_bg};
    border: none;
    border-radius: 6px;
    text-align: center;
    color: {text_primary};
}}

QProgressBar::chunk {{
    background-color: {accent};
    border-radius: 6px;
}}

QToolTip {{
    background-color: {surface_bg};
    color: {text_primary};
    border: 1px solid {border};
    padding: 4px 8px;
    border-radius: 6px;
}}
"""


def build_stylesheet(theme: Theme) -> str:
    """Render the full application QSS for ``theme``.

    Args:
        theme: Which palette to render (:attr:`config.Theme.LIGHT` or
            :attr:`config.Theme.DARK`).

    Returns:
        A complete QSS document suitable for
        ``QApplication.setStyleSheet``.
    """
    palette = _PALETTES[theme]
    ui_config = get_config().ui
    language = get_config().locale.default_language
    font_family = (
        ui_config.default_font_family_ar
        if language.value == "ar"
        else ui_config.default_font_family_en
    )
    base_size = ui_config.default_font_size
    return _QSS_TEMPLATE.format(
        font_family=font_family,
        font_size=base_size,
        font_size_lg=base_size + 1,
        font_size_xl=base_size + 6,
        window_bg=palette.window_bg,
        surface_bg=palette.surface_bg,
        surface_bg_hover=palette.surface_bg_hover,
        sidebar_bg=palette.sidebar_bg,
        sidebar_bg_hover=palette.sidebar_bg_hover,
        sidebar_bg_selected=palette.sidebar_bg_selected,
        border=palette.border,
        border_strong=palette.border_strong,
        text_primary=palette.text_primary,
        text_secondary=palette.text_secondary,
        text_on_accent=palette.text_on_accent,
        accent=palette.accent,
        accent_hover=palette.accent_hover,
        accent_pressed=palette.accent_pressed,
        success=palette.success,
        warning=palette.warning,
        danger=palette.danger,
        danger_hover=palette.danger_hover,
        scrollbar_handle=palette.scrollbar_handle,
    )


def get_palette(theme: Theme) -> Palette:
    """Return the raw color :class:`Palette` for ``theme``.

    Useful for widgets that need a color value directly (e.g. drawing a
    custom chart) rather than through QSS.

    Args:
        theme: Which palette to return.

    Returns:
        The matching :class:`Palette`.
    """
    return _PALETTES[theme]


class ThemeManager(QObject):
    """Owns the application's active :class:`~config.Theme` and applies it.

    A single instance (see :func:`get_theme_manager`) is bound once to
    the running ``QApplication`` in ``main.py`` via :meth:`bind_application`;
    any window can call :meth:`toggle_theme` (e.g. from a settings
    screen) and every other window updates immediately, since
    ``QApplication.setStyleSheet`` re-cascades to the whole widget tree.
    """

    theme_changed = Signal(str)
    """Emitted with the new theme's value (``"light"`` or ``"dark"``)
    whenever :meth:`set_theme` actually changes the active theme."""

    def __init__(self) -> None:
        """Initialize with the configured default theme, unbound from any app."""
        super().__init__()
        self._lock = threading.Lock()
        self._current_theme: Theme = get_config().ui.default_theme
        self._app: QApplication | None = None

    @property
    def current_theme(self) -> Theme:
        """The currently active :class:`~config.Theme`."""
        with self._lock:
            return self._current_theme

    @property
    def current_palette(self) -> Palette:
        """The :class:`Palette` for the currently active theme."""
        return get_palette(self.current_theme)

    def bind_application(self, app: QApplication) -> None:
        """Attach this manager to the running application and apply the theme.

        Args:
            app: The running ``QApplication`` instance.
        """
        self._app = app
        self._apply_theme(self._current_theme)

    def set_theme(self, theme: Theme) -> None:
        """Switch the active theme.

        A no-op if ``theme`` is already active. Otherwise re-renders and
        applies the stylesheet, then emits :attr:`theme_changed`.

        Args:
            theme: The theme to switch to.
        """
        with self._lock:
            if theme is self._current_theme:
                return
            self._current_theme = theme

        self._apply_theme(theme)
        self.theme_changed.emit(theme.value)

    def toggle_theme(self) -> None:
        """Switch between :attr:`~config.Theme.LIGHT` and :attr:`~config.Theme.DARK`."""
        next_theme = Theme.DARK if self.current_theme is Theme.LIGHT else Theme.LIGHT
        self.set_theme(next_theme)

    def _apply_theme(self, theme: Theme) -> None:
        if self._app is not None:
            self._app.setStyleSheet(build_stylesheet(theme))


_theme_manager: ThemeManager | None = None
_manager_lock = threading.Lock()


def get_theme_manager() -> ThemeManager:
    """Return the process-wide :class:`ThemeManager` singleton.

    Thread-safe first-time construction, matching the pattern used by
    :func:`utils.i18n.get_locale_manager`.
    """
    global _theme_manager
    if _theme_manager is None:
        with _manager_lock:
            if _theme_manager is None:
                _theme_manager = ThemeManager()
    return _theme_manager

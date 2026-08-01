"""Runtime language switching, RTL/LTR layout, and locale-aware formatting.

This module has two halves:

* :class:`LocaleManager` — the single source of truth for which
  language is active. It integrates with Qt's own translation
  machinery (:class:`~PySide6.QtCore.QTranslator`) so the UI layer can
  support future ``.ts``/``.qm`` translation files produced by Qt
  Linguist *without any source code change* — new languages are added
  by dropping a compiled ``.qm`` file into
  ``assets/translations/`` and adding the language to
  :attr:`config.LocaleConfig.supported_languages`.
* Free ``format_*`` functions — locale-aware date/time/number/currency
  formatting that has no Qt dependency at all, so it is equally usable
  from the UI, from ``services/report_service.py``, and from the
  Excel/PDF export utilities.

Source-language convention
---------------------------
This application is Arabic-first: UI strings are written directly in
Arabic in the widget source code (``self.tr("حفظ")``), so Arabic is the
*source* language and never needs a ``.qm`` file — its strings display
as-is. Switching to English loads
``assets/translations/attendance_en.qm``.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, time
from decimal import Decimal

from PySide6.QtCore import QCoreApplication, QObject, Qt, QTranslator, Signal

from config import Language, get_config

_WESTERN_TO_ARABIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def to_arabic_numerals(text: str) -> str:
    """Convert Western digits (0-9) in ``text`` to Eastern Arabic-Indic numerals.

    Args:
        text: Text possibly containing Western digits.

    Returns:
        ``text`` with every digit replaced by its Arabic-Indic
        equivalent (e.g. ``"2026-01-05"`` -> ``"٢٠٢٦-٠١-٠٥"``).
    """
    return text.translate(_WESTERN_TO_ARABIC_DIGITS)


class LocaleManager(QObject):
    """Owns the application's current language and Qt layout direction.

    A single instance (see :func:`get_locale_manager`) is bound once to
    the running :class:`~PySide6.QtCore.QCoreApplication` in ``main.py``
    via :meth:`bind_application`; every window/widget can then listen to
    :attr:`language_changed` to re-translate itself
    (``self.retranslateUi()``) when the user switches languages at
    runtime.
    """

    language_changed = Signal(str)
    """Emitted with the new language code (``"ar"`` or ``"en"``) whenever
    :meth:`set_language` actually changes the active language."""

    SOURCE_LANGUAGE = Language.ARABIC
    """The language UI strings are written in directly in source code.
    Never has a ``.qm`` file loaded for it — its strings display as-is."""

    def __init__(self) -> None:
        """Initialize with the configured default language, unbound from any app."""
        super().__init__()
        self._lock = threading.Lock()
        self._current_language: Language = get_config().locale.default_language
        self._app: QCoreApplication | None = None
        self._translator = QTranslator()

    def bind_application(self, app: QCoreApplication) -> None:
        """Attach this manager to the running application.

        Applies the current language's translator and layout direction
        immediately. Must be called once, early in ``main.py``, after
        the ``QApplication`` is constructed.

        Args:
            app: The running Qt application instance.
        """
        self._app = app
        self._apply_language(self._current_language)

    @property
    def current_language(self) -> Language:
        """The currently active :class:`~config.Language`."""
        with self._lock:
            return self._current_language

    @property
    def is_rtl(self) -> bool:
        """Whether the current language renders right-to-left."""
        return self.current_language is Language.ARABIC

    @property
    def layout_direction(self) -> Qt.LayoutDirection:
        """The Qt layout direction matching the current language."""
        return Qt.RightToLeft if self.is_rtl else Qt.LeftToRight

    def set_language(self, language: Language) -> None:
        """Switch the active language.

        A no-op if ``language`` is already active. Otherwise installs
        (or removes) the matching Qt translator, updates the
        application's layout direction, and emits
        :attr:`language_changed`.

        Args:
            language: The language to switch to.

        Raises:
            ValueError: If ``language`` is not in
                :attr:`config.LocaleConfig.supported_languages`.
        """
        supported = get_config().locale.supported_languages
        if language not in supported:
            supported_codes = [item.value for item in supported]
            raise ValueError(
                f"Unsupported language {language.value!r}; "
                f"supported languages are {supported_codes}."
            )

        with self._lock:
            if language is self._current_language:
                return
            self._current_language = language

        self._apply_language(language)
        self.language_changed.emit(language.value)

    def _apply_language(self, language: Language) -> None:
        """Install/remove the Qt translator and set layout direction.

        ``setLayoutDirection`` only exists on ``QGuiApplication`` and up
        — not on the base ``QCoreApplication`` — so this module can
        still be bound to a headless ``QCoreApplication`` (e.g. a future
        API-only process that only needs translated strings, not a GUI)
        without crashing; the layout-direction step is simply skipped
        when the bound application does not support it.
        """
        if self._app is None:
            return

        self._app.removeTranslator(self._translator)
        if language is not self.SOURCE_LANGUAGE:
            qm_path = get_config().paths.translations_dir / f"attendance_{language.value}.qm"
            if qm_path.exists() and self._translator.load(str(qm_path)):
                self._app.installTranslator(self._translator)

        set_layout_direction = getattr(self._app, "setLayoutDirection", None)
        if callable(set_layout_direction):
            set_layout_direction(
                Qt.RightToLeft if language is Language.ARABIC else Qt.LeftToRight
            )


_locale_manager: LocaleManager | None = None
_manager_lock = threading.Lock()


def get_locale_manager() -> LocaleManager:
    """Return the process-wide :class:`LocaleManager` singleton.

    Thread-safe first-time construction, matching the pattern used by
    :func:`config.get_config` and :func:`database.database.get_database`.
    """
    global _locale_manager
    if _locale_manager is None:
        with _manager_lock:
            if _locale_manager is None:
                _locale_manager = LocaleManager()
    return _locale_manager


def get_current_language() -> Language:
    """Convenience shortcut for ``get_locale_manager().current_language``."""
    return get_locale_manager().current_language


# ---------------------------------------------------------------------------
# Locale-aware formatting (no Qt dependency)
# ---------------------------------------------------------------------------


def format_date(value: date, *, language: Language | None = None) -> str:
    """Format a date using :attr:`config.LocaleConfig.date_format`.

    Args:
        value: The date to format.
        language: Overrides the current language for numeral rendering;
            defaults to :func:`get_current_language`.

    Returns:
        The formatted date string (e.g. ``"05/01/2026"``, or
        ``"٠٥/٠١/٢٠٢٦"`` when the active language is Arabic).
    """
    resolved_language = language or get_current_language()
    formatted = value.strftime(get_config().locale.date_format)
    if resolved_language is Language.ARABIC:
        formatted = to_arabic_numerals(formatted)
    return formatted


def format_time(value: time, *, language: Language | None = None) -> str:
    """Format a time using :attr:`config.LocaleConfig.time_format`.

    Args:
        value: The time to format.
        language: Overrides the current language for numeral rendering;
            defaults to :func:`get_current_language`.

    Returns:
        The formatted time string (e.g. ``"14:30"``, or ``"١٤:٣٠"``
        when the active language is Arabic).
    """
    resolved_language = language or get_current_language()
    formatted = value.strftime(get_config().locale.time_format)
    if resolved_language is Language.ARABIC:
        formatted = to_arabic_numerals(formatted)
    return formatted


def format_datetime(value: datetime, *, language: Language | None = None) -> str:
    """Format a datetime as ``"<date> <time>"`` using the configured formats.

    Args:
        value: The datetime to format.
        language: Overrides the current language; defaults to
            :func:`get_current_language`.

    Returns:
        The combined formatted date and time string.
    """
    resolved_language = language or get_current_language()
    return (
        f"{format_date(value.date(), language=resolved_language)} "
        f"{format_time(value.time(), language=resolved_language)}"
    )


def format_number(
    value: int | float | Decimal,
    *,
    decimal_places: int = 0,
    language: Language | None = None,
) -> str:
    """Format a number with thousands separators.

    Args:
        value: The number to format.
        decimal_places: How many digits to show after the decimal point.
        language: Overrides the current language for numeral rendering;
            defaults to :func:`get_current_language`.

    Returns:
        The formatted number (e.g. ``"1,500,000"``, or ``"١٬٥٠٠٬٠٠٠"``
        when the active language is Arabic — Arabic-Indic numerals with
        the Arabic thousands separator).
    """
    resolved_language = language or get_current_language()
    formatted = f"{Decimal(value):,.{decimal_places}f}"
    if resolved_language is Language.ARABIC:
        formatted = to_arabic_numerals(formatted).replace(",", "٬")
    return formatted


def format_currency(value: Decimal, *, language: Language | None = None) -> str:
    """Format a monetary amount with the company's currency symbol.

    Args:
        value: The amount to format.
        language: Overrides the current language; defaults to
            :func:`get_current_language`.

    Returns:
        The formatted amount followed by
        :attr:`config.LocaleConfig.currency_symbol` (e.g.
        ``"1,500,000.00 د.ع"``).
    """
    resolved_language = language or get_current_language()
    formatted_number = format_number(value, decimal_places=2, language=resolved_language)
    return f"{formatted_number} {get_config().locale.currency_symbol}"


def direction_for(language: Language) -> str:
    """Return ``"rtl"`` or ``"ltr"`` for the given language.

    Useful for non-Qt output (e.g. an HTML/PDF report's ``dir``
    attribute) that needs text direction without importing Qt.

    Args:
        language: The language to check.

    Returns:
        ``"rtl"`` for Arabic, ``"ltr"`` otherwise.
    """
    return "rtl" if language is Language.ARABIC else "ltr"

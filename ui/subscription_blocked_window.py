"""Subscription blocked screen: shown at startup whenever the subscription check disallows access.

The server-managed replacement for the retired
:class:`~ui.license_window.LicenseActivationWindow`. Unlike that
screen, this one has no activation form — subscriptions are only ever
created, renewed, suspended, or reactivated from the Developer Suite
(see :mod:`developer_suite.services.subscription_service`) — so this
screen only displays the clear reason access was denied (e.g.
"Subscription expired", "Company suspended", "Maximum devices
reached") and offers a "Retry" button to re-check with the server.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from services.subscription_check_service import SubscriptionCheckResult
from ui.widgets import Card, make_heading_label, make_primary_button, make_status_label


class SubscriptionBlockedWindow(QWidget):
    """The subscription-blocked screen.

    Emits :attr:`passed` once a re-check finds this installation
    allowed to proceed; the composition root (``main.py``) is
    responsible for closing this window and continuing startup in
    response. Closing this window *without* passing quits the whole
    application - there is nothing else to show.
    """

    passed = Signal()
    """Emitted once a re-check allows this installation to proceed."""

    def __init__(
        self,
        *,
        recheck: Callable[[], SubscriptionCheckResult],
        parent: QWidget | None = None,
    ) -> None:
        """Build the blocked window, showing the check's current message.

        Args:
            recheck: Called on the "Retry" button; re-attempts
                enrollment (if not yet enrolled) and re-checks the
                subscription (see ``main.py``'s composition of this
                callback).
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._recheck = recheck
        self._did_pass = False

        self.setWindowTitle("تعذّر الوصول - نظام إدارة الحضور والانصراف")
        self.setMinimumSize(560, 320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(20)

        layout.addWidget(make_heading_label("تعذّر الوصول إلى النظام"))

        self._message_label = make_status_label("", "danger")
        self._message_label.setWordWrap(True)
        layout.addWidget(self._message_label)

        retry_card = Card(parent=self)
        self.retry_button = make_primary_button("إعادة المحاولة", parent=retry_card)
        self.retry_button.clicked.connect(self._on_retry_clicked)
        retry_card.body_layout.addWidget(self.retry_button)
        layout.addWidget(retry_card)

        layout.addStretch(1)

    def show_result(self, message_ar: str) -> None:
        """Display ``message_ar`` as the current block reason."""
        self._message_label.setText(message_ar)

    def _on_retry_clicked(self) -> None:
        """Re-check with the server; proceed if now allowed."""
        result = self._recheck()
        if result.allowed:
            self._did_pass = True
            self.passed.emit()
            return
        self.show_result(result.message_ar)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Quit the whole application if this window is closed without passing.

        The composition root closes this window itself right after a
        successful re-check (see ``main.py``), which must not be
        mistaken for the user closing it via the window manager - the
        ``_did_pass`` flag distinguishes the two, exactly like
        :class:`~ui.login_window.LoginWindow`'s ``_did_succeed`` does
        for the same reason.
        """
        super().closeEvent(event)
        if not self._did_pass:
            app = QApplication.instance()
            if app is not None:
                app.quit()

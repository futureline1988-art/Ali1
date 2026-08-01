"""License activation screen: shown at startup whenever no valid license is active.

Independent of every other screen in ``ui/`` — it talks only to
:class:`~licensing.license_service.LicenseService`, never to a
controller, service, or repository from the rest of the application,
since it must be usable before any company, user, or even the database
schema exists.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from licensing.enums import LicenseStatusCode
from licensing.license_service import (
    InvalidLicenseKeyError,
    LicenseMachineMismatchError,
    LicenseService,
    TrialAlreadyUsedError,
)
from licensing.machine_id import format_machine_id_for_display
from ui.widgets import (
    Card,
    make_heading_label,
    make_primary_button,
    make_secondary_label,
    make_status_label,
)

_STATUS_LABEL_STYLE = {
    LicenseStatusCode.NOT_ACTIVATED: "warning",
    LicenseStatusCode.EXPIRED: "danger",
    LicenseStatusCode.MACHINE_MISMATCH: "danger",
    LicenseStatusCode.INVALID: "danger",
    LicenseStatusCode.VALID: "success",
}


class LicenseActivationWindow(QWidget):
    """The license activation screen.

    Emits :attr:`activated` once a valid license (paid key or new
    trial) has been established; the composition root (``main.py``) is
    responsible for closing this window and proceeding to the normal
    login flow in response. Closing this window *without* activating
    quits the whole application - there is nothing else to show.
    """

    activated = Signal()
    """Emitted once activation (key or trial) succeeds."""

    def __init__(self, *, license_service: LicenseService, parent: QWidget | None = None) -> None:
        """Build the activation window and show the current license status.

        Args:
            license_service: The service this window activates through.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._service = license_service
        self._did_activate = False

        self.setWindowTitle("تفعيل الترخيص - نظام إدارة الحضور والانصراف")
        self.setMinimumSize(560, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(20)

        layout.addWidget(make_heading_label("تفعيل الترخيص"))

        self._current_status_label = make_status_label("", "warning")
        self._current_status_label.setWordWrap(True)
        layout.addWidget(self._current_status_label)

        layout.addWidget(self._build_machine_id_card())
        layout.addWidget(self._build_activation_card())

        self._error_label = make_status_label("", "danger")
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        layout.addWidget(self._error_label)

        layout.addStretch(1)

        self._refresh_status()

    # ------------------------------------------------------------------
    # Layout construction
    # ------------------------------------------------------------------

    def _build_machine_id_card(self) -> Card:
        """Build the card displaying this machine's fingerprint, with a copy button."""
        card = Card(parent=self)
        card.body_layout.addWidget(make_secondary_label("معرّف هذا الجهاز (Machine ID)"))

        row = QHBoxLayout()
        self.machine_id_edit = QLineEdit(card)
        self.machine_id_edit.setText(format_machine_id_for_display(self._service.machine_id))
        self.machine_id_edit.setReadOnly(True)
        self.machine_id_edit.setAlignment(Qt.AlignCenter)
        row.addWidget(self.machine_id_edit)

        copy_button = make_primary_button("نسخ", parent=card)
        copy_button.clicked.connect(self._on_copy_machine_id_clicked)
        row.addWidget(copy_button)

        card.body_layout.addLayout(row)
        card.body_layout.addWidget(
            make_secondary_label("أرسل هذا المعرّف إلى المورّد للحصول على مفتاح ترخيص مرتبط بهذا الجهاز.")
        )
        return card

    def _build_activation_card(self) -> Card:
        """Build the card with the license-key input and activation actions."""
        card = Card(parent=self)
        form = QFormLayout()
        form.setSpacing(10)

        self.license_key_edit = QPlainTextEdit(card)
        self.license_key_edit.setPlaceholderText("الصق مفتاح الترخيص هنا...")
        self.license_key_edit.setFixedHeight(90)
        form.addRow("مفتاح الترخيص", self.license_key_edit)
        card.body_layout.addLayout(form)

        actions_row = QHBoxLayout()
        self.activate_button = make_primary_button("تفعيل", parent=card)
        self.activate_button.clicked.connect(self._on_activate_clicked)
        actions_row.addWidget(self.activate_button)

        self.trial_button = make_primary_button("بدء نسخة تجريبية (14 يومًا)", parent=card)
        self.trial_button.clicked.connect(self._on_start_trial_clicked)
        actions_row.addWidget(self.trial_button)

        card.body_layout.addLayout(actions_row)
        return card

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _refresh_status(self) -> None:
        """Re-check and display the current license status and trial availability."""
        status = self._service.get_status()
        self._current_status_label.setProperty(
            "status", _STATUS_LABEL_STYLE[status.code]
        )
        style = self._current_status_label.style()
        style.unpolish(self._current_status_label)
        style.polish(self._current_status_label)
        self._current_status_label.setText(status.message_ar)

        self.trial_button.setEnabled(self._service.is_trial_available())

    def _show_error(self, message: str) -> None:
        """Display an inline error banner."""
        self._error_label.setText(message)
        self._error_label.show()

    def _hide_error(self) -> None:
        """Hide the inline error banner."""
        self._error_label.clear()
        self._error_label.hide()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_copy_machine_id_clicked(self) -> None:
        """Copy the raw (unformatted) machine ID to the clipboard."""
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._service.machine_id)

    def _on_activate_clicked(self) -> None:
        """Verify and activate the pasted license key."""
        self._hide_error()
        key_text = self.license_key_edit.toPlainText().strip()
        if not key_text:
            self._show_error("الرجاء لصق مفتاح الترخيص.")
            return

        try:
            self._service.activate(key_text)
        except InvalidLicenseKeyError:
            self._show_error("مفتاح الترخيص غير صالح أو تالف.")
            return
        except LicenseMachineMismatchError:
            self._show_error("هذا المفتاح مرتبط بجهاز آخر ولا يمكن استخدامه على هذا الجهاز.")
            return

        self._complete_activation()

    def _on_start_trial_clicked(self) -> None:
        """Start the one-time local trial license."""
        self._hide_error()
        try:
            self._service.start_trial()
        except TrialAlreadyUsedError:
            self._show_error("تم استخدام الفترة التجريبية على هذا الجهاز مسبقًا.")
            self._refresh_status()
            return

        self._complete_activation()

    def _complete_activation(self) -> None:
        """Common success path for both activation methods."""
        self._did_activate = True
        self._refresh_status()
        self.activated.emit()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Quit the whole application if this window is closed without activating.

        There is nothing else this process can meaningfully show
        without a valid license - unlike every other window in this
        application, closing this one is not a "go back" action.
        """
        super().closeEvent(event)
        if not self._did_activate:
            app = QApplication.instance()
            if app is not None:
                app.quit()

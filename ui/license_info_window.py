"""License Information screen: view, renew, transfer, and manage the active license.

Unlike :class:`~ui.license_window.LicenseActivationWindow` (shown once,
before login, gating application startup), this screen is meant to be
reachable at any time while the application is running - for ongoing
license management and customer support - and is embedded as a tab in
``ui/settings.py``. It talks only to
:class:`~licensing.license_service.LicenseService`, same as the
activation window, keeping the licensing UI independent of every other
part of the application.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from licensing.enums import LicenseStatusCode, LicenseType
from licensing.license_service import (
    InvalidLicenseKeyError,
    InvalidRenewalTypeError,
    LicenseMachineMismatchError,
    LicenseService,
    NoActiveLicenseError,
    NoRenewableLicenseError,
    TrialNotTransferableError,
)
from licensing.machine_id import format_machine_id_for_display
from ui.widgets import (
    Card,
    ConfirmDialog,
    make_danger_button,
    make_heading_label,
    make_primary_button,
    make_secondary_label,
    make_status_label,
)
from utils.i18n import format_date, format_datetime

_STATUS_LABEL_STYLE = {
    LicenseStatusCode.NOT_ACTIVATED: "warning",
    LicenseStatusCode.EXPIRED: "danger",
    LicenseStatusCode.MACHINE_MISMATCH: "danger",
    LicenseStatusCode.INVALID: "danger",
    LicenseStatusCode.VALID: "success",
}

_RENEWABLE_TYPES = (LicenseType.MONTHLY, LicenseType.YEARLY)

_EMPTY_VALUE = "—"


class _LicenseKeyInputDialog(QDialog):
    """A focused modal prompt for pasting a license key.

    Shared by the "Activate License" and "Renew License" actions - both
    need nothing more than one key string and an OK/Cancel choice.
    """

    def __init__(self, *, title: str, hint: str, parent: QWidget | None = None) -> None:
        """Build the dialog.

        Args:
            title: The dialog window's title.
            hint: Short instructional text shown above the input.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(make_secondary_label(hint))

        self._key_edit = QPlainTextEdit(self)
        self._key_edit.setPlaceholderText("الصق مفتاح الترخيص هنا...")
        self._key_edit.setFixedHeight(90)
        layout.addWidget(self._key_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.button(QDialogButtonBox.Ok).setText("موافق")
        buttons.button(QDialogButtonBox.Cancel).setText("إلغاء")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def key_text(self) -> str:
        """The pasted key, stripped of surrounding whitespace."""
        return self._key_edit.toPlainText().strip()


class LicenseInfoWindow(QWidget):
    """Displays full license details and exposes every management action."""

    license_changed = Signal()
    """Emitted whenever the stored license changes (activate/renew/deactivate)."""

    def __init__(
        self, *, license_service: LicenseService | None = None, parent: QWidget | None = None
    ) -> None:
        """Build the license information screen.

        Args:
            license_service: The service this screen operates through;
                defaults to a new
                :class:`~licensing.license_service.LicenseService`.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._service = license_service or LicenseService()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        layout.addWidget(make_heading_label("معلومات الترخيص"))

        self._status_banner = make_status_label("", "success")
        self._status_banner.setWordWrap(True)
        self._status_banner.hide()
        layout.addWidget(self._status_banner)

        layout.addWidget(self._build_info_card())
        layout.addWidget(self._build_actions_card())
        layout.addStretch(1)

        self.refresh()

    # ------------------------------------------------------------------
    # Layout construction
    # ------------------------------------------------------------------

    def _build_info_card(self) -> Card:
        """Build the card showing the 8 read-only license detail fields."""
        card = Card(parent=self)
        form = QFormLayout()
        form.setSpacing(10)

        self._company_name_label = make_secondary_label(_EMPTY_VALUE)
        form.addRow("اسم الشركة", self._company_name_label)

        self._customer_name_label = make_secondary_label(_EMPTY_VALUE)
        form.addRow("اسم العميل", self._customer_name_label)

        self._license_type_label = make_secondary_label(_EMPTY_VALUE)
        form.addRow("نوع الترخيص", self._license_type_label)

        self._license_status_label = make_status_label(_EMPTY_VALUE, "warning")
        form.addRow("حالة الترخيص", self._license_status_label)

        self._activated_at_label = make_secondary_label(_EMPTY_VALUE)
        form.addRow("تاريخ التفعيل", self._activated_at_label)

        self._expires_at_label = make_secondary_label(_EMPTY_VALUE)
        form.addRow("تاريخ الانتهاء", self._expires_at_label)

        machine_id_row = QHBoxLayout()
        self.machine_id_edit = QLineEdit(card)
        self.machine_id_edit.setReadOnly(True)
        self.machine_id_edit.setAlignment(Qt.AlignCenter)
        machine_id_row.addWidget(self.machine_id_edit)
        copy_button = make_primary_button("نسخ", parent=card)
        copy_button.clicked.connect(self._on_copy_machine_id_clicked)
        machine_id_row.addWidget(copy_button)
        form.addRow("معرّف الجهاز (Machine ID)", machine_id_row)

        self._license_id_label = make_secondary_label(_EMPTY_VALUE)
        self._license_id_label.setWordWrap(True)
        form.addRow("معرّف الترخيص (License ID)", self._license_id_label)

        card.body_layout.addLayout(form)
        return card

    def _build_actions_card(self) -> Card:
        """Build the card with the 5 management action buttons."""
        card = Card(parent=self)
        row = QHBoxLayout()
        row.setSpacing(10)

        self.activate_button = make_primary_button("تفعيل ترخيص", parent=card)
        self.activate_button.clicked.connect(self._on_activate_clicked)
        row.addWidget(self.activate_button)

        self.renew_button = make_primary_button("تجديد الترخيص", parent=card)
        self.renew_button.clicked.connect(self._on_renew_clicked)
        row.addWidget(self.renew_button)

        self.export_request_button = make_primary_button("تصدير طلب الترخيص", parent=card)
        self.export_request_button.clicked.connect(self._on_export_request_clicked)
        row.addWidget(self.export_request_button)

        self.deactivate_button = make_danger_button("إلغاء تفعيل الترخيص", parent=card)
        self.deactivate_button.clicked.connect(self._on_deactivate_clicked)
        row.addWidget(self.deactivate_button)

        self.copy_machine_id_button = make_primary_button("نسخ معرّف الجهاز", parent=card)
        self.copy_machine_id_button.clicked.connect(self._on_copy_machine_id_clicked)
        row.addWidget(self.copy_machine_id_button)

        card.body_layout.addLayout(row)
        return card

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Reload the license details from the service and repaint every field."""
        self._hide_banner()
        details = self._service.get_details()
        status = details.status

        self._company_name_label.setText(details.company_name or _EMPTY_VALUE)
        self._customer_name_label.setText(details.customer_name or _EMPTY_VALUE)
        self._license_type_label.setText(
            status.license_type.label_ar if status.license_type is not None else _EMPTY_VALUE
        )

        self._license_status_label.setProperty("status", _STATUS_LABEL_STYLE[status.code])
        style = self._license_status_label.style()
        style.unpolish(self._license_status_label)
        style.polish(self._license_status_label)
        self._license_status_label.setText(status.code.label_ar)

        self._activated_at_label.setText(
            format_datetime(details.activated_at) if details.activated_at else _EMPTY_VALUE
        )
        self._expires_at_label.setText(
            format_date(status.expires_at) if status.expires_at else _EMPTY_VALUE
        )
        self.machine_id_edit.setText(format_machine_id_for_display(details.machine_id))
        self._license_id_label.setText(details.license_id or _EMPTY_VALUE)

        self.renew_button.setEnabled(status.license_type in _RENEWABLE_TYPES)
        has_active_license = status.code is not LicenseStatusCode.NOT_ACTIVATED
        self.deactivate_button.setEnabled(has_active_license)
        self.export_request_button.setEnabled(
            has_active_license and status.license_type is not LicenseType.TRIAL
        )

    def _show_banner(self, message: str, *, status: str) -> None:
        """Display a status banner (success or error)."""
        self._status_banner.setProperty("status", status)
        style = self._status_banner.style()
        style.unpolish(self._status_banner)
        style.polish(self._status_banner)
        self._status_banner.setText(message)
        self._status_banner.show()

    def _hide_banner(self) -> None:
        """Hide the status banner."""
        self._status_banner.clear()
        self._status_banner.hide()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_copy_machine_id_clicked(self) -> None:
        """Copy the raw (unformatted) machine ID to the clipboard."""
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._service.machine_id)
            self._show_banner("تم نسخ معرّف الجهاز.", status="success")

    def _on_activate_clicked(self) -> None:
        """Prompt for a key and activate it, replacing any current license."""
        dialog = _LicenseKeyInputDialog(
            title="تفعيل ترخيص جديد",
            hint="الصق مفتاح الترخيص المستلم من المورّد. سيحل محل أي ترخيص حالي على هذا الجهاز.",
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        key_text = dialog.key_text()
        if not key_text:
            self._show_banner("الرجاء لصق مفتاح الترخيص.", status="danger")
            return

        try:
            self._service.activate(key_text)
        except InvalidLicenseKeyError:
            self._show_banner("مفتاح الترخيص غير صالح أو تالف.", status="danger")
            return
        except LicenseMachineMismatchError:
            self._show_banner("هذا المفتاح مرتبط بجهاز آخر ولا يمكن استخدامه على هذا الجهاز.", status="danger")
            return

        self.refresh()
        self._show_banner("تم تفعيل الترخيص بنجاح.", status="success")
        self.license_changed.emit()

    def _on_renew_clicked(self) -> None:
        """Prompt for a new key and renew the current Monthly/Yearly license with it."""
        dialog = _LicenseKeyInputDialog(
            title="تجديد الترخيص",
            hint="الصق مفتاح التجديد الجديد المستلم من المورّد.",
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        key_text = dialog.key_text()
        if not key_text:
            self._show_banner("الرجاء لصق مفتاح الترخيص.", status="danger")
            return

        try:
            status = self._service.renew(key_text)
        except NoRenewableLicenseError:
            self._show_banner("لا يوجد ترخيص شهري أو سنوي قابل للتجديد على هذا الجهاز.", status="danger")
            return
        except InvalidRenewalTypeError:
            self._show_banner("مفتاح التجديد يجب أن يكون اشتراكًا شهريًا أو سنويًا.", status="danger")
            return
        except InvalidLicenseKeyError:
            self._show_banner("مفتاح الترخيص غير صالح أو تالف.", status="danger")
            return
        except LicenseMachineMismatchError:
            self._show_banner("هذا المفتاح مرتبط بجهاز آخر ولا يمكن استخدامه على هذا الجهاز.", status="danger")
            return

        self.refresh()
        expiry_text = format_date(status.expires_at) if status.expires_at else _EMPTY_VALUE
        self._show_banner(f"تم تجديد الترخيص بنجاح. تاريخ الانتهاء الجديد: {expiry_text}", status="success")
        self.license_changed.emit()

    def _on_export_request_clicked(self) -> None:
        """Save the current license's transfer request to a file the customer sends to the vendor."""
        chosen, _filter = QFileDialog.getSaveFileName(
            self, "تصدير طلب الترخيص", "license_transfer_request.json", "JSON Files (*.json)"
        )
        if not chosen:
            return

        try:
            self._service.export_transfer_request(Path(chosen))
        except NoActiveLicenseError:
            self._show_banner("لا يوجد ترخيص فعّال لتصدير طلب بشأنه.", status="danger")
            return
        except TrialNotTransferableError:
            self._show_banner("لا يمكن نقل النسخة التجريبية إلى جهاز آخر.", status="danger")
            return
        except InvalidLicenseKeyError:
            self._show_banner("بيانات الترخيص المخزنة غير صالحة، تعذّر تصدير الطلب.", status="danger")
            return

        self._show_banner(f"تم تصدير طلب الترخيص إلى: {chosen}", status="success")

    def _on_deactivate_clicked(self) -> None:
        """Confirm, optionally export a transfer request, then deactivate the current license."""
        confirmed = ConfirmDialog.confirm(
            self,
            "تأكيد إلغاء تفعيل الترخيص",
            "سيتم إلغاء تفعيل الترخيص على هذا الجهاز فورًا، ولن يعمل التطبيق بعد إعادة التشغيل حتى "
            "يُفعَّل ترخيص جديد. يمكنك حفظ ملف طلب نقل لإرساله إلى المورّد في الخطوة التالية. هل تريد المتابعة؟",
            danger=True,
        )
        if not confirmed:
            return

        chosen, _filter = QFileDialog.getSaveFileName(
            self,
            "حفظ ملف طلب النقل (اختياري - اضغط إلغاء لتخطي هذه الخطوة)",
            "license_transfer_request.json",
            "JSON Files (*.json)",
        )
        transfer_path = Path(chosen) if chosen else None

        try:
            self._service.deactivate(export_transfer_request_to=transfer_path)
        except TrialNotTransferableError:
            # The user asked to also export a request for a trial license -
            # deactivate the trial anyway, just without the (impossible) export.
            self._service.deactivate()
            self.refresh()
            self._show_banner(
                "تم إلغاء تفعيل الترخيص. تعذّر تصدير طلب نقل للنسخة التجريبية (غير قابلة للنقل).",
                status="danger",
            )
            self.license_changed.emit()
            return

        self.refresh()
        message = "تم إلغاء تفعيل الترخيص بنجاح."
        if transfer_path is not None:
            message += f" وتم حفظ طلب النقل إلى: {transfer_path}"
        self._show_banner(message, status="success")
        self.license_changed.emit()

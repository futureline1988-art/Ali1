"""License Key dialog: shown right after issuing or renewing a license.

The one missing step in the issuance workflow this closes: before this
dialog existed, :class:`~developer_suite.ui.license_management_page.LicenseManagementPage`
issued (or renewed) a license and just reloaded the table — the signed
:attr:`~developer_suite.models.license.IssuedLicense.license_key` string
was never shown anywhere, so there was no way to actually hand it to a
customer, even though issuance itself had fully succeeded. This dialog
is that missing hand-off point: it shows the full key read-only, with
a one-click Copy to clipboard and an Export to a ``.lic`` text file,
either of which is enough for the customer to paste into the
Attendance Client's own activation screen
(:class:`ui.license_window.LicenseWindow`, which only ever accepts a
pasted key -- see its own ``license_key_edit`` field).

The same key is still retrievable later, without re-issuing anything,
from :class:`~developer_suite.ui.license_details_dialog.LicenseDetailsDialog`
("View Details" on any row in the license table) -- this dialog is
just the immediate, no-extra-clicks path right after the action that
produced the key.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from developer_suite.models.license import IssuedLicense


class LicenseKeyDialog(QDialog):
    """Displays one license's signed key, with Copy and Export actions."""

    def __init__(self, license_record: IssuedLicense, *, parent: QWidget | None = None) -> None:
        """Build the dialog for ``license_record``.

        Args:
            license_record: The just-issued or just-renewed license
                whose :attr:`~developer_suite.models.license.IssuedLicense.license_key`
                this dialog shows.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._license_record = license_record
        self.setWindowTitle(f"مفتاح الترخيص — {license_record.customer.company_name}")
        self.setMinimumSize(520, 340)

        layout = QVBoxLayout(self)

        info_form = QFormLayout()
        info_form.addRow("العميل", QLabel(license_record.customer.company_name, self))
        info_form.addRow("نوع الترخيص", QLabel(license_record.license_type.label_ar, self))
        info_form.addRow("معرّف الجهاز", QLabel(license_record.machine_id or "غير مقيّد بجهاز", self))
        layout.addLayout(info_form)

        layout.addWidget(QLabel("مفتاح الترخيص (انسخه أو صدّره وأرسله إلى العميل):", self))

        self.key_edit = QPlainTextEdit(self)
        self.key_edit.setPlainText(license_record.license_key)
        self.key_edit.setReadOnly(True)
        self.key_edit.setFixedHeight(140)
        layout.addWidget(self.key_edit)

        actions_row = QHBoxLayout()
        self.copy_button = QPushButton("نسخ مفتاح الترخيص", self)
        self.copy_button.clicked.connect(self._on_copy_clicked)
        actions_row.addWidget(self.copy_button)

        self.export_button = QPushButton("تصدير إلى ملف...", self)
        self.export_button.clicked.connect(self._on_export_clicked)
        actions_row.addWidget(self.export_button)

        actions_row.addStretch(1)

        close_button = QPushButton("إغلاق", self)
        close_button.clicked.connect(self.accept)
        actions_row.addWidget(close_button)

        layout.addLayout(actions_row)

    def _on_copy_clicked(self) -> None:
        """Copy the raw license key to the system clipboard."""
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._license_record.license_key)
        self.copy_button.setText("تم النسخ ✓")

    def _on_export_clicked(self) -> None:
        """Write the raw license key to a plain-text file the customer can open and paste from."""
        default_name = f"{self._license_record.customer.company_name}.lic"
        output_path, _selected_filter = QFileDialog.getSaveFileName(
            self, "تصدير مفتاح الترخيص", default_name, "License files (*.lic);;All Files (*)"
        )
        if not output_path:
            return

        try:
            self.export_to_file(Path(output_path))
        except OSError as exc:
            QMessageBox.warning(self, "تعذّر التصدير", str(exc))
            return
        QMessageBox.information(self, "تم التصدير", f"تم حفظ مفتاح الترخيص في:\n{output_path}")

    def export_to_file(self, output_path: Path) -> Path:
        """Write the raw license key text to ``output_path``.

        Separated from :meth:`_on_export_clicked` so the actual write
        behavior is directly testable without driving Qt's native
        save-file dialog.

        Returns:
            ``output_path``, for convenience.
        """
        output_path.write_text(self._license_record.license_key + "\n", encoding="utf-8")
        return output_path

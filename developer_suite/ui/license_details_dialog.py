"""License Details dialog: current status plus that customer's license history.

Talks only to :class:`~developer_suite.services.license_service.LicenseService`
— matching this platform's established service/UI boundary.

"Activation history" is presented here as the customer's full
:class:`~developer_suite.models.license.IssuedLicense` record history
(every license key ever issued to them, via
:meth:`~developer_suite.services.license_service.LicenseService.list_by_customer`)
rather than a per-activation-event log, because no such log exists
anywhere in this codebase: the client-side license store keeps only
the single currently-active record (see ``licensing/license_store.py``),
and :meth:`~developer_suite.services.license_service.LicenseService.renew_license`
updates a license row in place rather than appending a new one. This
is the most complete, honest view of licensing history the existing
data actually supports — introducing a genuine per-activation audit
log would be new persistent state, out of this phase's "reuse
existing services, no duplicated business logic" scope.
"""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from developer_suite.models.license import IssuedLicense
from developer_suite.services.license_service import LicenseService


def _status_label(license_record: IssuedLicense) -> str:
    if license_record.is_expired:
        return "منتهي الصلاحية"
    if license_record.is_active:
        return "نشط"
    return "ملغى"


def _days_remaining_label(license_record: IssuedLicense) -> str:
    days = license_record.days_remaining
    return "بلا حدود" if days is None else str(days)


class LicenseDetailsDialog(QDialog):
    """A read-focused dialog covering one license and its customer's full license history."""

    def __init__(
        self,
        license_record: IssuedLicense,
        license_service: LicenseService,
        *,
        parent: QWidget | None = None,
    ) -> None:
        """Build the dialog for one license record.

        Args:
            license_record: The license to show as "current."
            license_service: Source of that license's customer's full
                license history.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle(f"تفاصيل الترخيص — {license_record.customer.company_name}")
        self.setMinimumSize(560, 480)

        history = license_service.list_by_customer(license_record.customer_id)

        layout = QVBoxLayout(self)
        tabs = QTabWidget(self)
        layout.addWidget(tabs)

        tabs.addTab(self._build_current_tab(license_record), "الترخيص الحالي")
        tabs.addTab(self._build_history_tab(history), "سجل التراخيص")

    def _build_current_tab(self, license_record: IssuedLicense) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)

        form = QFormLayout()
        form.addRow("العميل", QLabel(license_record.customer.company_name, page))
        form.addRow("نوع الترخيص", QLabel(license_record.license_type.label_ar, page))
        form.addRow("الحالة الحالية", QLabel(_status_label(license_record), page))
        form.addRow("الأيام المتبقية", QLabel(_days_remaining_label(license_record), page))
        form.addRow(
            "تاريخ الانتهاء",
            QLabel(license_record.expires_at.isoformat() if license_record.expires_at else "بلا حدود", page),
        )
        form.addRow("الإصدار المرخّص", QLabel(license_record.licensed_version or "غير مقيّد", page))
        form.addRow("معرّف الجهاز", QLabel(license_record.machine_id or "—", page))
        layout.addLayout(form)

        layout.addWidget(QLabel("مفتاح الترخيص:", page))
        key_edit = QPlainTextEdit(license_record.license_key, page)
        key_edit.setReadOnly(True)
        key_edit.setFixedHeight(110)
        layout.addWidget(key_edit)

        copy_button = QPushButton("نسخ مفتاح الترخيص", page)
        copy_button.clicked.connect(
            lambda: self._copy_to_clipboard(license_record.license_key, copy_button)
        )
        layout.addWidget(copy_button)

        return page

    def _copy_to_clipboard(self, text: str, button: QPushButton) -> None:
        """Copy ``text`` to the system clipboard and briefly confirm on ``button``."""
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        button.setText("تم النسخ ✓")

    def _build_history_tab(self, history: list[IssuedLicense]) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)

        machine_ids = sorted({license_record.machine_id for license_record in history if license_record.machine_id})
        layout.addWidget(
            QLabel("قائمة الأجهزة: " + (", ".join(machine_ids) if machine_ids else "لا توجد"), page)
        )

        columns = ("نوع الترخيص", "الحالة", "تاريخ الإصدار", "تاريخ الانتهاء", "معرّف الجهاز")
        ordered_history = sorted(history, key=lambda license_record: license_record.issued_at, reverse=True)
        table = QTableWidget(len(ordered_history), len(columns), page)
        table.setHorizontalHeaderLabels(columns)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row, license_record in enumerate(ordered_history):
            table.setItem(row, 0, QTableWidgetItem(license_record.license_type.label_ar))
            table.setItem(row, 1, QTableWidgetItem(_status_label(license_record)))
            table.setItem(row, 2, QTableWidgetItem(license_record.issued_at.isoformat()))
            table.setItem(
                row,
                3,
                QTableWidgetItem(
                    license_record.expires_at.isoformat() if license_record.expires_at else "بلا حدود"
                ),
            )
            table.setItem(row, 4, QTableWidgetItem(license_record.machine_id or "—"))
        layout.addWidget(table)
        return page

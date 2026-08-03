"""Customer Details dialog: a complete, read-focused view of one customer.

Talks only to :class:`~developer_suite.services.license_service.LicenseService`
(for that customer's licenses) and
:class:`~developer_suite.sync.coordinator.SyncCoordinator` (for that
customer's own synchronization state, via the fully generic
:meth:`~developer_suite.sync.coordinator.SyncCoordinator.get_entity_sync_state`)
— never a repository directly, matching this platform's established
service/UI boundary.

"Installed version" and "last online time" are shown as explicitly
unavailable rather than guessed: no relationship exists anywhere in
this codebase between a :class:`~developer_suite.models.customer.Customer`
row and a specific registered Attendance Client
:class:`~server.models.device.SyncDevice` (see this platform's
architecture notes on why that link was deliberately not fabricated
for Phase 10 — a name-matching heuristic would be unreliable, and a
new schema relationship would be a real synchronization-layer change,
out of this phase's explicit scope).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from developer_suite.models.customer import Customer
from developer_suite.models.license import IssuedLicense
from developer_suite.models.sync_state import OutboxStatus
from developer_suite.services.license_service import LicenseService
from developer_suite.sync.coordinator import SyncCoordinator
from developer_suite.sync.customer_sync import ENTITY_TYPE as CUSTOMER_ENTITY_TYPE

_NOT_AVAILABLE = "غير متاح — لا يوجد جهاز نظام حضور مرتبط بهذا العميل بعد"

_OUTBOX_STATUS_LABELS_AR = {
    OutboxStatus.PENDING: "بانتظار الإرسال",
    OutboxStatus.CONFLICT: "تعارض غير محلول",
    OutboxStatus.REJECTED: "مرفوض",
    OutboxStatus.PUSHED: "تم الإرسال",
}


def _license_status_label(license_record: IssuedLicense) -> str:
    if license_record.is_expired:
        return "منتهي الصلاحية"
    if license_record.is_active:
        return "نشط"
    return "ملغى"


class CustomerDetailsDialog(QDialog):
    """A read-focused dialog covering everything known about one customer."""

    def __init__(
        self,
        customer: Customer,
        license_service: LicenseService,
        sync_coordinator: SyncCoordinator,
        *,
        parent: QWidget | None = None,
    ) -> None:
        """Build the dialog for one customer.

        Args:
            customer: The customer to display.
            license_service: Source of this customer's license history.
            sync_coordinator: Source of this customer's own
                synchronization status.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle(f"بيانات العميل — {customer.company_name}")
        self.setMinimumSize(560, 480)

        licenses = license_service.list_by_customer(customer.id)
        sync_state = sync_coordinator.get_entity_sync_state(CUSTOMER_ENTITY_TYPE, str(customer.public_id))

        layout = QVBoxLayout(self)
        tabs = QTabWidget(self)
        layout.addWidget(tabs)

        tabs.addTab(self._build_info_tab(customer, sync_state), "معلومات عامة")
        tabs.addTab(self._build_licenses_tab(licenses), "التراخيص")

    def _build_info_tab(self, customer: Customer, sync_state) -> QWidget:
        page = QWidget(self)
        form = QFormLayout(page)

        form.addRow("اسم الشركة", QLabel(customer.company_name, page))
        form.addRow("العنوان", QLabel(customer.address or "—", page))
        form.addRow("جهة الاتصال", QLabel(customer.contact_name, page))
        form.addRow("الهاتف", QLabel(customer.phone or "—", page))
        form.addRow("البريد الإلكتروني", QLabel(customer.email or "—", page))
        form.addRow("الحالة", QLabel("نشط" if customer.is_active else "موقوف", page))

        form.addRow("الإصدار المثبت", QLabel(_NOT_AVAILABLE, page))
        form.addRow("آخر ظهور متصل", QLabel(_NOT_AVAILABLE, page))
        form.addRow("آخر مزامنة", QLabel(f"الإصدار المعروف: {sync_state.known_version}", page))
        form.addRow("حالة المزامنة", QLabel(_sync_status_text(sync_state), page))

        notes_edit = QPlainTextEdit(page)
        notes_edit.setPlainText(customer.notes or "")
        notes_edit.setReadOnly(True)
        notes_edit.setFixedHeight(80)
        form.addRow("ملاحظات", notes_edit)

        return page

    def _build_licenses_tab(self, licenses: list[IssuedLicense]) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)

        machine_ids = sorted({license_record.machine_id for license_record in licenses if license_record.machine_id})
        layout.addWidget(
            QLabel("معرّفات الأجهزة: " + (", ".join(machine_ids) if machine_ids else "لا توجد"), page)
        )

        columns = ("النوع", "الحالة", "تاريخ الإصدار", "تاريخ الانتهاء", "معرّف الجهاز")
        table = QTableWidget(len(licenses), len(columns), page)
        table.setHorizontalHeaderLabels(columns)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row, license_record in enumerate(licenses):
            table.setItem(row, 0, QTableWidgetItem(license_record.license_type.label_ar))
            table.setItem(row, 1, QTableWidgetItem(_license_status_label(license_record)))
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


def _sync_status_text(sync_state) -> str:
    if sync_state.pending_status is None:
        return "لا توجد تغييرات معلقة"
    label = _OUTBOX_STATUS_LABELS_AR.get(sync_state.pending_status, sync_state.pending_status.value)
    if sync_state.pending_conflict_reason:
        return f"{label} — {sync_state.pending_conflict_reason}"
    return label

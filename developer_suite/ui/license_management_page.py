"""License Manager page: search, list, issue, renew, revoke."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from developer_suite.models.license import IssuedLicense, IssuedLicenseStatus
from developer_suite.services.customer_service import CustomerService
from developer_suite.services.license_service import (
    LicenseNotFoundError,
    LicenseService,
    LicenseServiceError,
)
from developer_suite.ui.license_form_dialog import LicenseFormDialog

_COLUMN_LABELS = ("الشركة", "نوع الترخيص", "تاريخ الإصدار", "تاريخ الانتهاء", "الأيام المتبقية", "الحالة")


def _status_label(license_record: IssuedLicense) -> str:
    """The Arabic status word to show for one license row."""
    if license_record.status is IssuedLicenseStatus.REVOKED:
        return "ملغى"
    if license_record.is_expired:
        return "منتهي الصلاحية"
    return "نشط"


def _days_remaining_label(license_record: IssuedLicense) -> str:
    """The Arabic "days remaining" cell text for one license row."""
    days = license_record.days_remaining
    if days is None:
        return "بلا حدود"
    return str(days)


class LicenseManagementPage(QWidget):
    """The License Manager module's main content page.

    Talks only to
    :class:`~developer_suite.services.license_service.LicenseService`
    (and, to populate the "issue license" customer picker,
    :class:`~developer_suite.services.customer_service.CustomerService`)
    — never to the repositories directly, matching
    :class:`~developer_suite.ui.customer_management_page.CustomerManagementPage`'s
    established service/UI boundary.
    """

    def __init__(
        self,
        license_service: LicenseService,
        customer_service: CustomerService,
        *,
        parent: QWidget | None = None,
    ) -> None:
        """Build the page and load the initial, unfiltered license list.

        Args:
            license_service: The service every license operation goes
                through.
            customer_service: Used only to populate the customer picker
                in the "issue new license" dialog.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._license_service = license_service
        self._customer_service = customer_service
        self._licenses: list[IssuedLicense] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("بحث بالشركة أو معرّف الجهاز...")
        self.search_edit.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self.search_edit, stretch=1)

        self.add_button = QPushButton("إصدار ترخيص جديد", self)
        self.add_button.clicked.connect(self._on_add_clicked)
        toolbar.addWidget(self.add_button)

        self.renew_button = QPushButton("تجديد", self)
        self.renew_button.clicked.connect(self._on_renew_clicked)
        toolbar.addWidget(self.renew_button)

        self.revoke_button = QPushButton("إلغاء الترخيص", self)
        self.revoke_button.clicked.connect(self._on_revoke_clicked)
        toolbar.addWidget(self.revoke_button)

        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(_COLUMN_LABELS), self)
        self.table.setHorizontalHeaderLabels(_COLUMN_LABELS)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        self.reload()

    def reload(self) -> None:
        """Reload the table from the current search text."""
        self._populate(self._license_service.search_licenses(self.search_edit.text()))

    def _populate(self, licenses: list[IssuedLicense]) -> None:
        """Fill the table with ``licenses``, replacing the current contents."""
        self._licenses = licenses
        self.table.setRowCount(len(licenses))
        for row, license_record in enumerate(licenses):
            self.table.setItem(row, 0, QTableWidgetItem(license_record.customer.company_name))
            self.table.setItem(row, 1, QTableWidgetItem(license_record.license_type.label_ar))
            self.table.setItem(row, 2, QTableWidgetItem(license_record.issued_at.isoformat()))
            self.table.setItem(
                row,
                3,
                QTableWidgetItem(
                    license_record.expires_at.isoformat() if license_record.expires_at else "بلا حدود"
                ),
            )
            self.table.setItem(row, 4, QTableWidgetItem(_days_remaining_label(license_record)))
            self.table.setItem(row, 5, QTableWidgetItem(_status_label(license_record)))

    def _selected_license(self) -> IssuedLicense | None:
        """The license backing the currently selected row, if any."""
        row = self.table.currentRow()
        if row < 0 or row >= len(self._licenses):
            return None
        return self._licenses[row]

    def _on_search_changed(self, _text: str) -> None:
        self.reload()

    def _on_add_clicked(self) -> None:
        customers = self._customer_service.search_customers()
        if not customers:
            QMessageBox.information(
                self, "إصدار ترخيص", "الرجاء إضافة عميل أولاً من صفحة إدارة العملاء."
            )
            return

        dialog = LicenseFormDialog(customers=customers, parent=self)
        if dialog.exec() != LicenseFormDialog.DialogCode.Accepted:
            return
        try:
            self._license_service.issue_license(**dialog.field_values())
        except LicenseServiceError as exc:
            QMessageBox.warning(self, "تعذّر إصدار الترخيص", str(exc))
            return
        self.reload()

    def _on_renew_clicked(self) -> None:
        license_record = self._selected_license()
        if license_record is None:
            QMessageBox.information(self, "تجديد", "الرجاء اختيار ترخيص أولاً.")
            return

        confirmed = QMessageBox.question(
            self,
            "تأكيد التجديد",
            f"هل تريد تجديد ترخيص «{license_record.customer.company_name}»؟",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return

        try:
            self._license_service.renew_license(license_record.id)
        except LicenseServiceError as exc:
            QMessageBox.warning(self, "تعذّر التجديد", str(exc))
            return
        self.reload()

    def _on_revoke_clicked(self) -> None:
        license_record = self._selected_license()
        if license_record is None:
            QMessageBox.information(self, "إلغاء", "الرجاء اختيار ترخيص أولاً.")
            return

        confirmed = QMessageBox.question(
            self,
            "تأكيد الإلغاء",
            f"هل تريد إلغاء ترخيص «{license_record.customer.company_name}»؟",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return

        try:
            self._license_service.revoke_license(license_record.id)
        except LicenseNotFoundError as exc:
            QMessageBox.warning(self, "تعذّر الإلغاء", str(exc))
            return
        self.reload()

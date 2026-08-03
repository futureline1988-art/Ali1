"""Customer Management page: search, list, add, edit, delete, suspend/reactivate."""

from __future__ import annotations

from PySide6.QtCore import Qt
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

from developer_suite.models.customer import Customer, CustomerStatus
from developer_suite.services.customer_service import (
    CustomerNotFoundError,
    CustomerService,
    CustomerServiceError,
)
from developer_suite.services.license_service import LicenseService
from developer_suite.sync.coordinator import SyncCoordinator
from developer_suite.ui.customer_details_dialog import CustomerDetailsDialog
from developer_suite.ui.customer_form_dialog import CustomerFormDialog

_COLUMN_LABELS = ("الشركة", "جهة الاتصال", "الهاتف", "البريد الإلكتروني", "الحالة")
_STATUS_LABELS_AR = {
    CustomerStatus.ACTIVE: "نشط",
    CustomerStatus.SUSPENDED: "موقوف",
}


class CustomerManagementPage(QWidget):
    """The Customer Management module's main content page.

    Talks only to :class:`~developer_suite.services.customer_service.CustomerService`
    — never to :class:`~developer_suite.repositories.customer_repository.CustomerRepository`
    directly, matching this platform's established service/UI boundary.
    """

    def __init__(
        self,
        customer_service: CustomerService,
        license_service: LicenseService,
        sync_coordinator: SyncCoordinator,
        *,
        parent: QWidget | None = None,
    ) -> None:
        """Build the page and load the initial, unfiltered customer list.

        Args:
            customer_service: The service this page performs every
                operation through.
            license_service: Passed through to
                :class:`~developer_suite.ui.customer_details_dialog.CustomerDetailsDialog`
                for its license-history tab.
            sync_coordinator: Passed through to
                :class:`~developer_suite.ui.customer_details_dialog.CustomerDetailsDialog`
                for its synchronization-status field.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._service = customer_service
        self._license_service = license_service
        self._sync_coordinator = sync_coordinator
        self._customers: list[Customer] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("بحث بالشركة أو جهة الاتصال...")
        self.search_edit.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self.search_edit, stretch=1)

        self.add_button = QPushButton("إضافة عميل", self)
        self.add_button.clicked.connect(self._on_add_clicked)
        toolbar.addWidget(self.add_button)

        self.edit_button = QPushButton("تعديل", self)
        self.edit_button.clicked.connect(self._on_edit_clicked)
        toolbar.addWidget(self.edit_button)

        self.delete_button = QPushButton("حذف", self)
        self.delete_button.clicked.connect(self._on_delete_clicked)
        toolbar.addWidget(self.delete_button)

        self.toggle_status_button = QPushButton("إيقاف/تفعيل", self)
        self.toggle_status_button.clicked.connect(self._on_toggle_status_clicked)
        toolbar.addWidget(self.toggle_status_button)

        self.details_button = QPushButton("عرض التفاصيل", self)
        self.details_button.clicked.connect(self._on_details_clicked)
        toolbar.addWidget(self.details_button)

        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(_COLUMN_LABELS), self)
        self.table.setHorizontalHeaderLabels(_COLUMN_LABELS)
        self.table.doubleClicked.connect(self._on_details_clicked)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        self.reload()

    def reload(self) -> None:
        """Reload the table from the current search text."""
        self._populate(self._service.search_customers(self.search_edit.text()))

    def _populate(self, customers: list[Customer]) -> None:
        """Fill the table with ``customers``, replacing the current contents."""
        self._customers = customers
        self.table.setRowCount(len(customers))
        for row, customer in enumerate(customers):
            self.table.setItem(row, 0, QTableWidgetItem(customer.company_name))
            self.table.setItem(row, 1, QTableWidgetItem(customer.contact_name))
            self.table.setItem(row, 2, QTableWidgetItem(customer.phone or ""))
            self.table.setItem(row, 3, QTableWidgetItem(customer.email or ""))
            self.table.setItem(row, 4, QTableWidgetItem(_STATUS_LABELS_AR[customer.status]))

    def _selected_customer(self) -> Customer | None:
        """The customer backing the currently selected row, if any."""
        row = self.table.currentRow()
        if row < 0 or row >= len(self._customers):
            return None
        return self._customers[row]

    def _on_search_changed(self, _text: str) -> None:
        self.reload()

    def _on_add_clicked(self) -> None:
        dialog = CustomerFormDialog(existing=None, parent=self)
        if dialog.exec() != CustomerFormDialog.DialogCode.Accepted:
            return
        try:
            self._service.create_customer(**dialog.field_values())
        except CustomerServiceError as exc:
            QMessageBox.warning(self, "تعذّرت الإضافة", str(exc))
            return
        self.reload()

    def _on_edit_clicked(self) -> None:
        customer = self._selected_customer()
        if customer is None:
            QMessageBox.information(self, "تعديل", "الرجاء اختيار عميل أولاً.")
            return

        dialog = CustomerFormDialog(existing=customer, parent=self)
        if dialog.exec() != CustomerFormDialog.DialogCode.Accepted:
            return
        try:
            self._service.update_customer(customer.id, **dialog.field_values())
        except CustomerServiceError as exc:
            QMessageBox.warning(self, "تعذّر التعديل", str(exc))
            return
        self.reload()

    def _on_delete_clicked(self) -> None:
        customer = self._selected_customer()
        if customer is None:
            QMessageBox.information(self, "حذف", "الرجاء اختيار عميل أولاً.")
            return

        confirmed = QMessageBox.question(
            self,
            "تأكيد الحذف",
            f"هل تريد حذف العميل «{customer.company_name}»؟",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return

        try:
            self._service.delete_customer(customer.id)
        except CustomerNotFoundError as exc:
            QMessageBox.warning(self, "تعذّر الحذف", str(exc))
            return
        self.reload()

    def _on_toggle_status_clicked(self) -> None:
        customer = self._selected_customer()
        if customer is None:
            QMessageBox.information(self, "الحالة", "الرجاء اختيار عميل أولاً.")
            return

        try:
            if customer.status is CustomerStatus.ACTIVE:
                self._service.suspend(customer.id)
            else:
                self._service.reactivate(customer.id)
        except CustomerServiceError as exc:
            QMessageBox.warning(self, "تعذّر تغيير الحالة", str(exc))
            return
        self.reload()

    def _on_details_clicked(self, *_args: object) -> None:
        """Open :class:`~developer_suite.ui.customer_details_dialog.CustomerDetailsDialog`.

        Connected to both the "View Details" button's ``clicked``
        (which passes a ``bool``) and the table's ``doubleClicked``
        (which passes a ``QModelIndex``) — neither argument is used,
        hence the catch-all signature.
        """
        customer = self._selected_customer()
        if customer is None:
            QMessageBox.information(self, "التفاصيل", "الرجاء اختيار عميل أولاً.")
            return
        dialog = CustomerDetailsDialog(customer, self._license_service, self._sync_coordinator, parent=self)
        dialog.exec()

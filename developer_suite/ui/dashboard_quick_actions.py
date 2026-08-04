"""Dashboard quick action buttons.

Every action here delegates to the same services and dialogs the
Customer Management page already uses — nothing here duplicates a
business operation or invents a second way to create a customer:

* "New Customer" reuses :class:`~developer_suite.ui.customer_form_dialog.CustomerFormDialog`
  and :meth:`~developer_suite.services.customer_service.CustomerService.create_customer`,
  exactly as :meth:`~developer_suite.ui.customer_management_page.CustomerManagementPage._on_add_clicked`
  does.
* "Suspend Customer" reuses :meth:`~developer_suite.services.customer_service.CustomerService.suspend`.
* "Open Monitoring"/"Open Remote Configuration"/"Open Update Manager"/
  "Open Subscription Manager" are pure navigation — this panel has no
  knowledge of what those modules do, only their stable
  :attr:`~developer_suite.modules.base.PlatformModule.module_id`\\ s,
  emitted via :attr:`QuickActionsPanel.navigate_requested` for
  :class:`~developer_suite.ui.main_window.MainWindow` to act on
  (mirroring how :class:`~developer_suite.ui.navigation.NavigationSidebar`
  already emits a module id rather than switching pages itself).
  Creating/renewing a subscription needs a full form (a date range plus
  device/user caps) that does not suit a one-click quick action the way
  "issue license" once did — the dedicated Subscription Manager page is
  the one place for that, not a second, duplicate quick-entry path.

Where an action needs to target one existing customer that isn't
already selected anywhere (suspending), this panel asks with a plain
:class:`~PySide6.QtWidgets.QInputDialog` picker rather than
introducing a new, duplicate list/table widget — a quick action is
meant to be quick.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QWidget,
)

from developer_suite.models.customer import CustomerStatus
from developer_suite.services.customer_service import CustomerService, CustomerServiceError
from developer_suite.ui.customer_form_dialog import CustomerFormDialog

_ACTION_COLUMNS = 4


class QuickActionsPanel(QWidget):
    """The dashboard's row of one-click platform actions.

    Attributes:
        action_completed: Emitted after any action successfully writes
            a change (new customer, suspended customer) — the
            dashboard page connects this to an immediate refresh, so
            the cards/charts reflect the change without waiting for
            the next scheduled tick.
        navigate_requested: Emitted with a
            :attr:`~developer_suite.modules.base.PlatformModule.module_id`
            when a "open ..." action is clicked.
    """

    action_completed = Signal()
    navigate_requested = Signal(str)

    def __init__(
        self,
        customer_service: CustomerService,
        *,
        parent: QWidget | None = None,
    ) -> None:
        """Build the quick actions panel.

        Args:
            customer_service: Backs "New Customer"/"Suspend Customer".
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._customer_service = customer_service

        box = QGroupBox("إجراءات سريعة", self)
        layout = QGridLayout(box)
        layout.setSpacing(8)

        actions: tuple[tuple[str, object], ...] = (
            ("عميل جديد", self._on_new_customer),
            ("إيقاف عميل", self._on_suspend_customer),
            ("فتح إدارة الاشتراكات", lambda: self.navigate_requested.emit("subscription_manager")),
            ("فتح المراقبة", lambda: self.navigate_requested.emit("monitoring")),
            ("فتح الإعداد عن بُعد", lambda: self.navigate_requested.emit("remote_configuration")),
            ("فتح إدارة التحديثات", lambda: self.navigate_requested.emit("update_manager")),
        )
        for index, (label, handler) in enumerate(actions):
            button = QPushButton(label, box)
            button.clicked.connect(handler)
            row, column = divmod(index, _ACTION_COLUMNS)
            layout.addWidget(button, row, column)

        outer = QGridLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(box)

    def _on_new_customer(self) -> None:
        dialog = CustomerFormDialog(existing=None, parent=self)
        if dialog.exec() != CustomerFormDialog.DialogCode.Accepted:
            return
        try:
            self._customer_service.create_customer(**dialog.field_values())
        except CustomerServiceError as exc:
            QMessageBox.warning(self, "تعذّرت الإضافة", str(exc))
            return
        self.action_completed.emit()

    def _on_suspend_customer(self) -> None:
        active_customers = [
            customer
            for customer in self._customer_service.search_customers()
            if customer.status is CustomerStatus.ACTIVE
        ]
        if not active_customers:
            QMessageBox.information(self, "إيقاف عميل", "لا يوجد عملاء نشطون لإيقافهم.")
            return
        labels = [f"{customer.company_name} (#{customer.id})" for customer in active_customers]
        label, accepted = QInputDialog.getItem(
            self, "إيقاف عميل", "اختر العميل المراد إيقافه:", labels, editable=False
        )
        if not accepted:
            return
        customer = active_customers[labels.index(label)]
        try:
            self._customer_service.suspend(customer.id)
        except CustomerServiceError as exc:
            QMessageBox.warning(self, "تعذّر الإيقاف", str(exc))
            return
        self.action_completed.emit()

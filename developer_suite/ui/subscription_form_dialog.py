"""Create-a-new-subscription form dialog.

Mirrors the retired ``LicenseFormDialog``'s own boundary exactly: this
dialog only ever collects the inputs for creating a *new* subscription
(the server-managed replacement for the old license-issuance flow) —
renewing, suspending, and reactivating an existing subscription are
single-click actions on an existing row (see
:class:`~developer_suite.ui.subscription_management_page.SubscriptionManagementPage`),
not something this dialog handles.

The company-name field is an editable combo box pre-populated from the
existing customer registry (:class:`~developer_suite.models.customer.Customer`)
for convenience -- picking an existing customer's name is the common
case -- but accepts free text too, since
:class:`~server.models.subscription.Subscription` has no foreign key
to :class:`~developer_suite.models.customer.Customer` at all (the two
live in separate schemas/databases; see
:mod:`server.models.subscription`'s own docstring for why a
subscription is identified by company name, matched at Attendance
Client device-registration time, not a shared numeric id).
"""

from __future__ import annotations

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QSpinBox,
    QWidget,
)

from developer_suite.models.customer import Customer

_DEFAULT_SUBSCRIPTION_DAYS = 365
_DEFAULT_MAX_DEVICES = 5


class SubscriptionFormDialog(QDialog):
    """Form for creating a new subscription for a company.

    Construct with the list of customers to offer in the company-name
    picker (the caller loads these from
    :class:`~developer_suite.services.customer_service.CustomerService`
    — this dialog never talks to a service directly). Read the entered
    values back via :meth:`field_values` after :meth:`exec` returns
    accepted.
    """

    def __init__(self, *, customers: list[Customer], parent: QWidget | None = None) -> None:
        """Build the create-subscription form.

        Args:
            customers: Every existing customer to offer as a
                convenience pick in the company-name field, in the
                order to display them.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("إنشاء اشتراك جديد")
        self.setMinimumWidth(420)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.company_name_combo = QComboBox(self)
        self.company_name_combo.setEditable(True)
        self.company_name_combo.addItems([customer.company_name for customer in customers])
        self.company_name_combo.setCurrentText("")
        form.addRow("اسم الشركة *", self.company_name_combo)

        self.start_date_edit = QDateEdit(self)
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate.currentDate())
        form.addRow("تاريخ بدء الاشتراك *", self.start_date_edit)

        self.end_date_edit = QDateEdit(self)
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDate(QDate.currentDate().addDays(_DEFAULT_SUBSCRIPTION_DAYS))
        form.addRow("تاريخ انتهاء الاشتراك *", self.end_date_edit)

        self.max_devices_spin = QSpinBox(self)
        self.max_devices_spin.setRange(1, 1000)
        self.max_devices_spin.setValue(_DEFAULT_MAX_DEVICES)
        form.addRow("الحد الأقصى للأجهزة *", self.max_devices_spin)

        self.max_users_spin = QSpinBox(self)
        self.max_users_spin.setRange(0, 100_000)
        self.max_users_spin.setSpecialValueText("بلا حدود")
        self.max_users_spin.setValue(0)
        form.addRow("الحد الأقصى للمستخدمين", self.max_users_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def field_values(self) -> dict[str, object]:
        """Read every field's current value.

        Returns:
            A dict with keys ``company_name``, ``subscription_start_date``,
            ``subscription_end_date``, ``max_devices``, ``max_users`` —
            ready to pass as keyword arguments to
            :meth:`~developer_suite.services.subscription_service.SubscriptionService.create_subscription`.
            ``max_users`` is ``None`` when left at its "unlimited"
            (``0``) value.
        """
        max_users_value = self.max_users_spin.value()
        return {
            "company_name": self.company_name_combo.currentText().strip(),
            "subscription_start_date": self.start_date_edit.date().toPython(),
            "subscription_end_date": self.end_date_edit.date().toPython(),
            "max_devices": self.max_devices_spin.value(),
            "max_users": max_users_value or None,
        }

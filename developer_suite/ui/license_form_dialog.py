"""Issue-a-new-license form dialog.

Unlike a customer record, an issued license's signed fields are not
something a user edits in place afterwards — re-signing is the only
way to change them, which is exactly what
:meth:`~developer_suite.services.license_service.LicenseService.renew_license`
does. This dialog therefore only ever collects the inputs for issuing
a *new* license; renewal and revocation are single-click actions on an
existing row (see
:class:`~developer_suite.ui.license_management_page.LicenseManagementPage`).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from developer_suite.models.customer import Customer
from licensing.enums import LicenseType


class LicenseFormDialog(QDialog):
    """Form for issuing a new license to one of the registered customers.

    Construct with the list of customers to offer in the picker (the
    caller loads these from
    :class:`~developer_suite.services.customer_service.CustomerService`
    — this dialog never talks to a service directly, matching
    :class:`~developer_suite.ui.customer_form_dialog.CustomerFormDialog`'s
    boundary). Read the entered values back via :meth:`field_values`
    after :meth:`exec` returns accepted.
    """

    def __init__(self, *, customers: list[Customer], parent: QWidget | None = None) -> None:
        """Build the issue-license form.

        Args:
            customers: Every customer selectable as the license
                recipient, in the order to display them.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._customers = customers
        self.setWindowTitle("إصدار ترخيص جديد")
        self.setMinimumWidth(420)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.customer_combo = QComboBox(self)
        for customer in customers:
            self.customer_combo.addItem(customer.company_name, userData=customer.id)
        form.addRow("العميل *", self.customer_combo)

        self.license_type_combo = QComboBox(self)
        for license_type in LicenseType:
            self.license_type_combo.addItem(license_type.label_ar, userData=license_type)
        form.addRow("نوع الترخيص *", self.license_type_combo)

        self.machine_id_edit = QLineEdit(self)
        self.machine_id_edit.setPlaceholderText("اختياري - ربط بجهاز محدد")
        form.addRow("معرّف الجهاز", self.machine_id_edit)

        self.licensed_version_edit = QLineEdit(self)
        self.licensed_version_edit.setPlaceholderText("اختياري، مثال: 1.2.0")
        form.addRow("أقصى إصدار مرخّص", self.licensed_version_edit)

        self.days_override_spin = QSpinBox(self)
        self.days_override_spin.setRange(0, 3650)
        self.days_override_spin.setSpecialValueText("افتراضي حسب نوع الترخيص")
        self.days_override_spin.setValue(0)
        form.addRow("مدة الصلاحية (أيام)", self.days_override_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def field_values(self) -> dict[str, object]:
        """Read every field's current value.

        Returns:
            A dict with keys ``customer_id``, ``license_type``,
            ``machine_id``, ``licensed_version``, ``days`` — ready to
            pass as keyword arguments to
            :meth:`~developer_suite.services.license_service.LicenseService.issue_license`.
            ``machine_id``/``licensed_version``/``days`` are ``None``
            when left at their blank/default value.
        """
        days_value = self.days_override_spin.value()
        return {
            "customer_id": self.customer_combo.currentData(),
            # Qt's item-data round-trip does not preserve a str-Enum
            # subclass's identity (it comes back a plain str equal in
            # value) - re-wrap explicitly so callers get a real
            # LicenseType member, not just a value-equal string.
            "license_type": LicenseType(self.license_type_combo.currentData()),
            "machine_id": self.machine_id_edit.text().strip() or None,
            "licensed_version": self.licensed_version_edit.text().strip() or None,
            "days": days_value or None,
        }

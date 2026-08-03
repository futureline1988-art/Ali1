"""Add/edit form dialog for a single customer."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QPlainTextEdit, QWidget

from developer_suite.models.customer import Customer


class CustomerFormDialog(QDialog):
    """Add/edit form for a single customer.

    Construct with ``existing=None`` for "add" mode, or an existing
    :class:`~developer_suite.models.customer.Customer` for "edit" mode,
    which pre-fills every field. Does not itself call
    :class:`~developer_suite.services.customer_service.CustomerService`
    — the caller reads the entered values back via :meth:`field_values`
    after :meth:`exec` returns accepted.
    """

    def __init__(self, *, existing: Customer | None = None, parent: QWidget | None = None) -> None:
        """Build the form dialog.

        Args:
            existing: The customer being edited, or ``None`` to create
                a new one.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._existing = existing
        self.setWindowTitle("تعديل بيانات عميل" if existing else "إضافة عميل جديد")
        self.setMinimumWidth(420)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.company_name_edit = QLineEdit(self)
        form.addRow("اسم الشركة *", self.company_name_edit)

        self.contact_name_edit = QLineEdit(self)
        form.addRow("اسم جهة الاتصال *", self.contact_name_edit)

        self.phone_edit = QLineEdit(self)
        form.addRow("الهاتف", self.phone_edit)

        self.email_edit = QLineEdit(self)
        form.addRow("البريد الإلكتروني", self.email_edit)

        self.address_edit = QLineEdit(self)
        form.addRow("العنوان", self.address_edit)

        self.notes_edit = QPlainTextEdit(self)
        self.notes_edit.setFixedHeight(80)
        form.addRow("ملاحظات", self.notes_edit)

        if existing is not None:
            self.company_name_edit.setText(existing.company_name)
            self.contact_name_edit.setText(existing.contact_name)
            self.phone_edit.setText(existing.phone or "")
            self.email_edit.setText(existing.email or "")
            self.address_edit.setText(existing.address or "")
            self.notes_edit.setPlainText(existing.notes or "")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def field_values(self) -> dict[str, str | None]:
        """Read every field's current value.

        Returns:
            A dict with keys ``company_name``, ``contact_name``,
            ``phone``, ``email``, ``address``, ``notes`` — the last
            four are ``None`` when left blank, ready to pass as
            keyword arguments to
            :meth:`~developer_suite.services.customer_service.CustomerService.create_customer`/
            :meth:`~developer_suite.services.customer_service.CustomerService.update_customer`.
        """
        return {
            "company_name": self.company_name_edit.text().strip(),
            "contact_name": self.contact_name_edit.text().strip(),
            "phone": self.phone_edit.text().strip() or None,
            "email": self.email_edit.text().strip() or None,
            "address": self.address_edit.text().strip() or None,
            "notes": self.notes_edit.toPlainText().strip() or None,
        }

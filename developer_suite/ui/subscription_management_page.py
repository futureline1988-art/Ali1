"""Subscription Manager page: search, list, create, renew, suspend/reactivate.

The server-managed replacement for the retired ``LicenseManagementPage``:
"Suspend"/"Reactivate" toggle
:attr:`~server.models.subscription.SubscriptionStatus` immediately, and
"Renew" extends :attr:`~server.models.subscription.Subscription.subscription_end_date`
— no signing, encoding, or file export is involved anywhere in this
flow (contrast with the old license-key issuance dialog this page
replaces), since a subscription's validity is a plain database row the
Attendance Server itself evaluates at every client login (see
``server/api/routers/subscriptions.py``'s
``GET /api/v1/subscription/status``), not a signed artifact handed to
the customer.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from developer_suite.admin.client import InitialAdminInfo, SubscriptionInfo
from developer_suite.services.customer_service import CustomerService
from developer_suite.services.subscription_service import SubscriptionService, SubscriptionServiceError
from developer_suite.ui.subscription_form_dialog import SubscriptionFormDialog

_COLUMN_LABELS = (
    "الشركة",
    "رمز الشركة",
    "الحالة",
    "تاريخ البدء",
    "تاريخ الانتهاء",
    "الأيام المتبقية",
    "الأجهزة",
    "الحد الأقصى للمستخدمين",
)


def _status_label(subscription: SubscriptionInfo) -> str:
    """The Arabic status word to show for one subscription row."""
    if subscription.is_expired:
        return "منتهي"
    if subscription.status == "suspended":
        return "موقوف"
    return "نشط"


def _device_count_label(subscription: SubscriptionInfo) -> str:
    count = subscription.device_count if subscription.device_count is not None else "؟"
    return f"{count} / {subscription.max_devices}"


def _max_users_label(subscription: SubscriptionInfo) -> str:
    return str(subscription.max_users) if subscription.max_users is not None else "بلا حدود"


class _RenewDialog(QDialog):
    """Tiny dialog collecting just the new end date for a renewal."""

    def __init__(self, current_end_date: date, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("تجديد الاشتراك")
        layout = QVBoxLayout(self)
        self.end_date_edit = QDateEdit(self)
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDate(QDate(current_end_date.year, current_end_date.month, current_end_date.day))
        layout.addWidget(self.end_date_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def new_end_date(self) -> date:
        return self.end_date_edit.date().toPython()


class _InitialAdminDialog(QDialog):
    """Collects username/full name/password for a subscription's initial Company Administrator.

    The only place this credential is ever entered — see
    :mod:`server.services.initial_admin_service`'s own docstring for
    why the Attendance Client can only ever download the result, never
    create it itself.
    """

    def __init__(self, current: InitialAdminInfo, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("تعيين المسؤول الأولي")
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)

        status_text = (
            f"المسؤول الحالي: {current.username} ({current.full_name})"
            if current.configured
            else "لم يتم تعيين مسؤول أولي لهذا الاشتراك بعد."
        )
        status_label = QLabel(status_text, self)
        status_label.setWordWrap(True)
        layout.addWidget(status_label)

        form = QFormLayout()
        self.username_edit = QLineEdit(self)
        form.addRow("اسم المستخدم", self.username_edit)
        self.full_name_edit = QLineEdit(self)
        form.addRow("الاسم الكامل", self.full_name_edit)
        self.password_edit = QLineEdit(self)
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("كلمة المرور", self.password_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def field_values(self) -> dict[str, str]:
        return {
            "username": self.username_edit.text().strip(),
            "full_name": self.full_name_edit.text().strip(),
            "password": self.password_edit.text(),
        }


class _SupportInfoDialog(QDialog):
    """Collects a subscription's Support Information, pre-filled from its current values.

    Synchronized to every Attendance Client of this company on their
    next successful check — see
    :mod:`server.models.subscription`'s own docstring.
    """

    def __init__(self, current: SubscriptionInfo, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("معلومات الدعم الفني")
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.phone_primary_edit = QLineEdit(current.support_phone_primary or "", self)
        form.addRow("الهاتف الرئيسي", self.phone_primary_edit)
        self.phone_secondary_edit = QLineEdit(current.support_phone_secondary or "", self)
        form.addRow("الهاتف الثانوي (اختياري)", self.phone_secondary_edit)
        self.whatsapp_edit = QLineEdit(current.support_whatsapp or "", self)
        form.addRow("واتساب", self.whatsapp_edit)
        self.email_edit = QLineEdit(current.support_email or "", self)
        form.addRow("البريد الإلكتروني (اختياري)", self.email_edit)
        self.hours_edit = QLineEdit(current.support_hours or "", self)
        form.addRow("ساعات العمل (اختياري)", self.hours_edit)
        self.message_edit = QLineEdit(current.support_message or "", self)
        form.addRow("رسالة الدعم (اختياري)", self.message_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def field_values(self) -> dict[str, str | None]:
        """Every field, with a blank input mapped to ``None`` (clears that field on save)."""
        return {
            "support_phone_primary": self.phone_primary_edit.text().strip() or None,
            "support_phone_secondary": self.phone_secondary_edit.text().strip() or None,
            "support_whatsapp": self.whatsapp_edit.text().strip() or None,
            "support_email": self.email_edit.text().strip() or None,
            "support_hours": self.hours_edit.text().strip() or None,
            "support_message": self.message_edit.text().strip() or None,
        }


class SubscriptionManagementPage(QWidget):
    """The Subscription Manager module's main content page.

    Talks only to
    :class:`~developer_suite.services.subscription_service.SubscriptionService`
    (and, to populate the "create subscription" company-name picker,
    :class:`~developer_suite.services.customer_service.CustomerService`)
    — never to a repository directly, matching
    :class:`~developer_suite.ui.customer_management_page.CustomerManagementPage`'s
    established service/UI boundary.
    """

    def __init__(
        self,
        subscription_service: SubscriptionService,
        customer_service: CustomerService,
        *,
        parent: QWidget | None = None,
    ) -> None:
        """Build the page and load the initial, unfiltered subscription list.

        Args:
            subscription_service: The service every subscription
                operation goes through.
            customer_service: Used only to populate the company-name
                picker in the "create subscription" dialog.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._subscription_service = subscription_service
        self._customer_service = customer_service
        self._subscriptions: list[SubscriptionInfo] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("بحث بالشركة...")
        self.search_edit.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self.search_edit, stretch=1)

        self.add_button = QPushButton("إنشاء اشتراك جديد", self)
        self.add_button.clicked.connect(self._on_add_clicked)
        toolbar.addWidget(self.add_button)

        self.renew_button = QPushButton("تجديد", self)
        self.renew_button.clicked.connect(self._on_renew_clicked)
        toolbar.addWidget(self.renew_button)

        self.suspend_button = QPushButton("إيقاف", self)
        self.suspend_button.clicked.connect(self._on_suspend_clicked)
        toolbar.addWidget(self.suspend_button)

        self.reactivate_button = QPushButton("إعادة تفعيل", self)
        self.reactivate_button.clicked.connect(self._on_reactivate_clicked)
        toolbar.addWidget(self.reactivate_button)

        self.initial_admin_button = QPushButton("تعيين المسؤول الأولي", self)
        self.initial_admin_button.clicked.connect(self._on_set_initial_admin_clicked)
        toolbar.addWidget(self.initial_admin_button)

        self.support_info_button = QPushButton("معلومات الدعم الفني", self)
        self.support_info_button.clicked.connect(self._on_support_info_clicked)
        toolbar.addWidget(self.support_info_button)

        layout.addLayout(toolbar)

        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, len(_COLUMN_LABELS), self)
        self.table.setHorizontalHeaderLabels(_COLUMN_LABELS)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        self.reload()

    def reload(self) -> None:
        """Reload the table, filtered by the current search text.

        A failure here is a *background* reload, not something the
        user just clicked a button to trigger — it goes to
        :attr:`status_label`, never a blocking
        :class:`~PySide6.QtWidgets.QMessageBox` (the same discipline
        :mod:`developer_suite.ui.update_manager_page` documents and
        follows for its own reload failures).
        """
        try:
            subscriptions = self._subscription_service.list_subscriptions()
            self.status_label.hide()
        except SubscriptionServiceError as exc:
            self.status_label.setText(f"تعذّر الاتصال بخادم الحضور: {exc}")
            self.status_label.show()
            subscriptions = []
        search = self.search_edit.text().strip().lower()
        if search:
            subscriptions = [
                record for record in subscriptions if search in record.company_name.lower()
            ]
        self._populate(subscriptions)

    def _populate(self, subscriptions: list[SubscriptionInfo]) -> None:
        """Fill the table with ``subscriptions``, replacing the current contents."""
        self._subscriptions = subscriptions
        self.table.setRowCount(len(subscriptions))
        for row, record in enumerate(subscriptions):
            self.table.setItem(row, 0, QTableWidgetItem(record.company_name))
            self.table.setItem(row, 1, QTableWidgetItem(record.company_code))
            self.table.setItem(row, 2, QTableWidgetItem(_status_label(record)))
            self.table.setItem(row, 3, QTableWidgetItem(record.subscription_start_date.isoformat()))
            self.table.setItem(row, 4, QTableWidgetItem(record.subscription_end_date.isoformat()))
            self.table.setItem(row, 5, QTableWidgetItem(str(record.days_remaining)))
            self.table.setItem(row, 6, QTableWidgetItem(_device_count_label(record)))
            self.table.setItem(row, 7, QTableWidgetItem(_max_users_label(record)))

    def _selected_subscription(self) -> SubscriptionInfo | None:
        """The subscription backing the currently selected row, if any."""
        row = self.table.currentRow()
        if row < 0 or row >= len(self._subscriptions):
            return None
        return self._subscriptions[row]

    def _on_search_changed(self, _text: str) -> None:
        self.reload()

    def _on_add_clicked(self) -> None:
        customers = self._customer_service.search_customers()
        dialog = SubscriptionFormDialog(customers=customers, parent=self)
        if dialog.exec() != SubscriptionFormDialog.DialogCode.Accepted:
            return
        values = dialog.field_values()
        if not values["company_name"]:
            QMessageBox.information(self, "إنشاء اشتراك", "الرجاء إدخال اسم الشركة.")
            return
        try:
            created = self._subscription_service.create_subscription(**values)
        except SubscriptionServiceError as exc:
            QMessageBox.warning(self, "تعذّر إنشاء الاشتراك", str(exc))
            return
        self.reload()
        # The one moment this code must be captured -- it is never shown
        # to the Attendance Client again except as this row's own column,
        # and the whole point of this migration is that the vendor hands
        # it to the company out of band, not that the client ever lists
        # or discovers it on its own.
        QMessageBox.information(
            self,
            "تم إنشاء الاشتراك",
            f"تم إنشاء الاشتراك بنجاح.\n\nرمز الشركة: {created.company_code}\n\n"
            "يرجى تسليم هذا الرمز لمسؤول الشركة لاستخدامه عند أول تشغيل لبرنامج الحضور.",
        )

    def _on_renew_clicked(self) -> None:
        subscription = self._selected_subscription()
        if subscription is None:
            QMessageBox.information(self, "تجديد", "الرجاء اختيار اشتراك أولاً.")
            return

        dialog = _RenewDialog(subscription.subscription_end_date, parent=self)
        if dialog.exec() != _RenewDialog.DialogCode.Accepted:
            return

        try:
            self._subscription_service.renew_subscription(
                subscription.id, new_end_date=dialog.new_end_date()
            )
        except SubscriptionServiceError as exc:
            QMessageBox.warning(self, "تعذّر التجديد", str(exc))
            return
        self.reload()

    def _on_suspend_clicked(self) -> None:
        subscription = self._selected_subscription()
        if subscription is None:
            QMessageBox.information(self, "إيقاف", "الرجاء اختيار اشتراك أولاً.")
            return

        confirmed = QMessageBox.question(
            self,
            "تأكيد الإيقاف",
            f"هل تريد إيقاف اشتراك «{subscription.company_name}»؟ لن تتمكن الشركة من تسجيل الدخول حتى إعادة التفعيل.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return

        try:
            self._subscription_service.suspend_subscription(subscription.id)
        except SubscriptionServiceError as exc:
            QMessageBox.warning(self, "تعذّر الإيقاف", str(exc))
            return
        self.reload()

    def _on_reactivate_clicked(self) -> None:
        subscription = self._selected_subscription()
        if subscription is None:
            QMessageBox.information(self, "إعادة تفعيل", "الرجاء اختيار اشتراك أولاً.")
            return

        try:
            self._subscription_service.reactivate_subscription(subscription.id)
        except SubscriptionServiceError as exc:
            QMessageBox.warning(self, "تعذّر إعادة التفعيل", str(exc))
            return
        self.reload()

    def _on_set_initial_admin_clicked(self) -> None:
        """Create or replace the selected subscription's initial Company Administrator.

        The Attendance Client for this company must never create this
        account itself -- this dialog is the one and only place it is
        ever set, downloaded from there afterward (see
        :mod:`server.services.initial_admin_service`'s own docstring).
        """
        subscription = self._selected_subscription()
        if subscription is None:
            QMessageBox.information(self, "تعيين المسؤول الأولي", "الرجاء اختيار اشتراك أولاً.")
            return

        try:
            current = self._subscription_service.get_initial_admin(subscription.id)
        except SubscriptionServiceError as exc:
            QMessageBox.warning(self, "تعذّر جلب بيانات المسؤول الأولي", str(exc))
            return

        dialog = _InitialAdminDialog(current, parent=self)
        if dialog.exec() != _InitialAdminDialog.DialogCode.Accepted:
            return
        values = dialog.field_values()
        if not values["username"] or not values["full_name"] or not values["password"]:
            QMessageBox.information(
                self, "تعيين المسؤول الأولي", "الرجاء تعبئة اسم المستخدم والاسم الكامل وكلمة المرور."
            )
            return

        try:
            self._subscription_service.set_initial_admin(subscription.id, **values)
        except SubscriptionServiceError as exc:
            QMessageBox.warning(self, "تعذّر تعيين المسؤول الأولي", str(exc))
            return
        QMessageBox.information(
            self,
            "تم تعيين المسؤول الأولي",
            "تم تعيين المسؤول الأولي بنجاح. سيتم تنزيله تلقائيًا عند أول تسجيل دخول لبرنامج الحضور بهذا الرمز.",
        )

    def _on_support_info_clicked(self) -> None:
        """Set the selected subscription's Support Information.

        Synchronized to every Attendance Client of this company on
        their next successful check — see
        :mod:`server.models.subscription`'s own docstring.
        """
        subscription = self._selected_subscription()
        if subscription is None:
            QMessageBox.information(self, "معلومات الدعم الفني", "الرجاء اختيار اشتراك أولاً.")
            return

        dialog = _SupportInfoDialog(subscription, parent=self)
        if dialog.exec() != _SupportInfoDialog.DialogCode.Accepted:
            return

        try:
            self._subscription_service.update_support_info(subscription.id, **dialog.field_values())
        except SubscriptionServiceError as exc:
            QMessageBox.warning(self, "تعذّر حفظ معلومات الدعم الفني", str(exc))
            return
        self.reload()

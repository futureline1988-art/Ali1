"""Customer Details dialog: a complete, read-focused view of one customer.

Talks only to :class:`~developer_suite.services.subscription_service.SubscriptionService`
(for that company's subscription — the server-managed replacement for
the retired file-based license system) and
:class:`~developer_suite.sync.coordinator.SyncCoordinator` (for that
customer's own synchronization state, via the fully generic
:meth:`~developer_suite.sync.coordinator.SyncCoordinator.get_entity_sync_state`)
— never a repository directly, matching this platform's established
service/UI boundary.

A :class:`~developer_suite.models.customer.Customer` and a
:class:`~server.models.subscription.Subscription` are two separate
records in two separate schemas/databases, linked only by
:attr:`~developer_suite.models.customer.Customer.company_name` matching
:attr:`~server.models.subscription.Subscription.company_name` exactly
(see :mod:`server.models.subscription`'s own docstring on why a
subscription is identified by company name rather than a numeric id a
customer's IT admin would need to relay at device registration) — this
dialog resolves that match itself rather than the two services sharing
a foreign key across schema boundaries.

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
    QLabel,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from developer_suite.admin.client import SubscriptionInfo
from developer_suite.models.customer import Customer
from developer_suite.models.sync_state import OutboxStatus
from developer_suite.services.subscription_service import SubscriptionService, SubscriptionServiceError
from developer_suite.sync.coordinator import SyncCoordinator
from developer_suite.sync.customer_sync import ENTITY_TYPE as CUSTOMER_ENTITY_TYPE

_NOT_AVAILABLE = "غير متاح — لا يوجد جهاز نظام حضور مرتبط بهذا العميل بعد"
_SUBSCRIPTION_UNAVAILABLE = "تعذّر الوصول إلى خادم الحضور لعرض بيانات الاشتراك."
_NO_SUBSCRIPTION = "لا يوجد اشتراك بهذا الاسم بعد. يمكن إنشاؤه من صفحة إدارة الاشتراكات."

_OUTBOX_STATUS_LABELS_AR = {
    OutboxStatus.PENDING: "بانتظار الإرسال",
    OutboxStatus.CONFLICT: "تعارض غير محلول",
    OutboxStatus.REJECTED: "مرفوض",
    OutboxStatus.PUSHED: "تم الإرسال",
}


def _subscription_status_label(subscription: SubscriptionInfo) -> str:
    if subscription.is_expired:
        return "منتهي"
    if subscription.status == "suspended":
        return "موقوف"
    return "نشط"


class CustomerDetailsDialog(QDialog):
    """A read-focused dialog covering everything known about one customer."""

    def __init__(
        self,
        customer: Customer,
        subscription_service: SubscriptionService,
        sync_coordinator: SyncCoordinator,
        *,
        parent: QWidget | None = None,
    ) -> None:
        """Build the dialog for one customer.

        Args:
            customer: The customer to display.
            subscription_service: Source of this customer's
                subscription, matched by company name (see this
                module's own docstring).
            sync_coordinator: Source of this customer's own
                synchronization status.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle(f"بيانات العميل — {customer.company_name}")
        self.setMinimumSize(560, 480)

        subscription = self._find_subscription(subscription_service, customer.company_name)
        sync_state = sync_coordinator.get_entity_sync_state(CUSTOMER_ENTITY_TYPE, str(customer.public_id))

        layout = QVBoxLayout(self)
        tabs = QTabWidget(self)
        layout.addWidget(tabs)

        tabs.addTab(self._build_info_tab(customer, sync_state), "معلومات عامة")
        tabs.addTab(self._build_subscription_tab(subscription), "الاشتراك")

    @staticmethod
    def _find_subscription(
        subscription_service: SubscriptionService, company_name: str
    ) -> SubscriptionInfo | None | str:
        """Look up the subscription matching ``company_name``.

        Returns:
            The matching :class:`~developer_suite.admin.client.SubscriptionInfo`,
            ``None`` if none exists yet, or the literal string
            ``"unavailable"`` if the Attendance Server could not be
            reached at all.
        """
        try:
            subscriptions = subscription_service.list_subscriptions()
        except SubscriptionServiceError:
            return "unavailable"
        for subscription in subscriptions:
            if subscription.company_name == company_name:
                return subscription
        return None

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

    def _build_subscription_tab(self, subscription: SubscriptionInfo | None | str) -> QWidget:
        page = QWidget(self)
        form = QFormLayout(page)

        if subscription == "unavailable":
            form.addRow(QLabel(_SUBSCRIPTION_UNAVAILABLE, page))
            return page
        if subscription is None:
            form.addRow(QLabel(_NO_SUBSCRIPTION, page))
            return page

        form.addRow("الحالة", QLabel(_subscription_status_label(subscription), page))
        form.addRow("تاريخ البدء", QLabel(subscription.subscription_start_date.isoformat(), page))
        form.addRow("تاريخ الانتهاء", QLabel(subscription.subscription_end_date.isoformat(), page))
        form.addRow("الأيام المتبقية", QLabel(str(subscription.days_remaining), page))
        form.addRow("الحد الأقصى للأجهزة", QLabel(str(subscription.max_devices), page))
        device_count = subscription.device_count if subscription.device_count is not None else "—"
        form.addRow("عدد الأجهزة الحالي", QLabel(str(device_count), page))
        max_users = subscription.max_users if subscription.max_users is not None else "بلا حدود"
        form.addRow("الحد الأقصى للمستخدمين", QLabel(str(max_users), page))

        return page


def _sync_status_text(sync_state) -> str:
    if sync_state.pending_status is None:
        return "لا توجد تغييرات معلقة"
    label = _OUTBOX_STATUS_LABELS_AR.get(sync_state.pending_status, sync_state.pending_status.value)
    if sync_state.pending_conflict_reason:
        return f"{label} — {sync_state.pending_conflict_reason}"
    return label

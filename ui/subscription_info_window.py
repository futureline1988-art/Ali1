"""Subscription Information screen: read-only view of this installation's subscription.

The server-managed replacement for the retired
:class:`~ui.license_info_window.LicenseInfoWindow`. Unlike the old
license screen, this one has no activate/renew/deactivate actions —
per this migration's requirement, the Developer Suite is now the only
place a subscription is created, renewed, suspended, or reactivated
(see :mod:`developer_suite.services.subscription_service`). This
screen only displays the last status
:class:`~services.subscription_check_service.SubscriptionCheckService`
confirmed, with a manual "Refresh" button to re-check now.

Embedded as a tab in ``ui/settings.py``, exactly like the old
``LicenseInfoWindow`` was.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QVBoxLayout, QWidget

from services.subscription_check_service import SubscriptionCheckOutcome, SubscriptionCheckService
from ui.widgets import Card, make_heading_label, make_primary_button, make_secondary_label, make_status_label
from utils.i18n import format_date

_EMPTY_VALUE = "—"

_STATUS_LABEL_STYLE = {
    SubscriptionCheckOutcome.VALID: "success",
    SubscriptionCheckOutcome.UNREACHABLE_WITHIN_GRACE: "warning",
    SubscriptionCheckOutcome.EXPIRED: "danger",
    SubscriptionCheckOutcome.SUSPENDED: "danger",
    SubscriptionCheckOutcome.NOT_REGISTERED: "danger",
    SubscriptionCheckOutcome.UNREACHABLE_BLOCKED: "danger",
}


class SubscriptionInfoWindow(QWidget):
    """Displays this installation's subscription status, read-only."""

    def __init__(self, *, check_service: SubscriptionCheckService, parent: QWidget | None = None) -> None:
        """Build the subscription information screen.

        Args:
            check_service: Performs the actual server check on
                :meth:`refresh` (see :meth:`~services.subscription_check_service.SubscriptionCheckService.check`).
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._service = check_service

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        layout.addWidget(make_heading_label("معلومات الاشتراك"))

        self._status_banner = make_status_label("", "success")
        self._status_banner.setWordWrap(True)
        layout.addWidget(self._status_banner)

        layout.addWidget(self._build_info_card())

        actions = QHBoxLayout()
        self.refresh_button = make_primary_button("تحديث الحالة", parent=self)
        self.refresh_button.clicked.connect(self.refresh)
        actions.addWidget(self.refresh_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addStretch(1)

        self.refresh()

    def _build_info_card(self) -> Card:
        """Build the card showing the read-only subscription detail fields."""
        card = Card(parent=self)
        form = QFormLayout()
        form.setSpacing(10)

        self._company_name_label = make_secondary_label(_EMPTY_VALUE)
        form.addRow("اسم الشركة", self._company_name_label)

        self._end_date_label = make_secondary_label(_EMPTY_VALUE)
        form.addRow("تاريخ انتهاء الاشتراك", self._end_date_label)

        self._days_remaining_label = make_secondary_label(_EMPTY_VALUE)
        form.addRow("الأيام المتبقية", self._days_remaining_label)

        card.body_layout.addLayout(form)
        return card

    def refresh(self) -> None:
        """Re-check the subscription with the server and repaint every field."""
        result = self._service.check()

        self._status_banner.setProperty("status", _STATUS_LABEL_STYLE[result.outcome])
        style = self._status_banner.style()
        style.unpolish(self._status_banner)
        style.polish(self._status_banner)
        self._status_banner.setText(result.message_ar)

        self._company_name_label.setText(result.company_name or _EMPTY_VALUE)
        self._days_remaining_label.setText(
            str(result.days_remaining) if result.days_remaining is not None else _EMPTY_VALUE
        )

        cached = self._service.get_cached()
        end_date_text = (
            format_date(cached.subscription_end_date)
            if cached is not None and cached.subscription_end_date
            else _EMPTY_VALUE
        )
        self._end_date_label.setText(end_date_text)

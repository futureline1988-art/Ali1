"""Contact Support dialog: read-only display of this company's Support Information.

Set entirely from the Developer Suite (see
:mod:`server.models.subscription`'s own docstring for the exact
fields) and delivered to this installation automatically on every
successful subscription check (see
:meth:`~services.subscription_check_service.SubscriptionCheckService.check`),
which caches it in :class:`~models.subscription_state.ClientSubscriptionState`
so it stays visible even while offline. This dialog only ever reads
that cache -- the Attendance Client has no way to set or change any of
these fields.
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QVBoxLayout, QWidget

from models.subscription_state import ClientSubscriptionState
from ui.widgets import make_heading_label, make_secondary_label


class SupportInfoDialog(QDialog):
    """Displays this company's cached Support Information, read-only.

    Construct with ``cached=None`` (a brand-new installation that has
    never completed a successful sync, or one whose last sync predates
    this feature) or with a row where every ``support_*`` field is
    unset -- either way, shows the "not configured" message instead of
    an empty form.
    """

    def __init__(
        self, *, cached: ClientSubscriptionState | None, parent: QWidget | None = None
    ) -> None:
        """Build the dialog from ``cached``'s ``support_*`` fields.

        Args:
            cached: The last server-confirmed subscription state (see
                :meth:`~services.subscription_check_service.SubscriptionCheckService.get_cached`),
                or ``None``.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("التواصل مع الدعم الفني")
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(make_heading_label("التواصل مع الدعم الفني"))

        fields = (
            ("الهاتف الرئيسي", cached.support_phone_primary if cached else None),
            ("الهاتف الثانوي", cached.support_phone_secondary if cached else None),
            ("واتساب", cached.support_whatsapp if cached else None),
            ("البريد الإلكتروني", cached.support_email if cached else None),
            ("ساعات العمل", cached.support_hours if cached else None),
        )
        populated_fields = [(label, value) for label, value in fields if value]
        message = cached.support_message if cached else None

        if not populated_fields and not message:
            empty_label = make_secondary_label("لم يتم إعداد معلومات دعم فني بعد.")
            empty_label.setWordWrap(True)
            layout.addWidget(empty_label)
        else:
            if populated_fields:
                form = QFormLayout()
                form.setSpacing(10)
                for label, value in populated_fields:
                    form.addRow(label, make_secondary_label(value))
                layout.addLayout(form)
            if message:
                message_label = make_secondary_label(message)
                message_label.setWordWrap(True)
                layout.addWidget(message_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        buttons.button(QDialogButtonBox.Close).setText("إغلاق")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

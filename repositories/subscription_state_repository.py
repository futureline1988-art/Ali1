"""Data access for the Attendance Client's local subscription-check bookkeeping.

Mirrors :mod:`repositories.sync_repository`'s
``ClientSyncCredentialRepository`` shape exactly: a small repository
over one singleton-row table (see :mod:`models.subscription_state`'s
own docstring).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.subscription_state import ClientSubscriptionState


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ClientSubscriptionStateRepository:
    """Data access for the singleton :class:`~models.subscription_state.ClientSubscriptionState` row."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        self.session = session

    def get(self) -> ClientSubscriptionState | None:
        """Return the last server-confirmed subscription state, or ``None`` if never checked."""
        return self.session.execute(select(ClientSubscriptionState)).scalars().first()

    def save(
        self,
        *,
        status: str,
        company_name: str | None,
        subscription_end_date: date | None,
        max_devices: int | None,
        days_remaining: int | None,
        support_phone_primary: str | None = None,
        support_phone_secondary: str | None = None,
        support_whatsapp: str | None = None,
        support_email: str | None = None,
        support_hours: str | None = None,
        support_message: str | None = None,
    ) -> ClientSubscriptionState:
        """Create or overwrite the singleton row with a freshly server-confirmed status.

        Args:
            status: The status the server just reported.
            company_name: The subscription's company name, as reported.
            subscription_end_date: The subscription's end date, as reported.
            max_devices: The subscription's device cap, as reported.
            days_remaining: Days remaining until expiry, as reported.
            support_phone_primary: This company's Support Information,
                as reported (see :mod:`models.subscription_state`'s
                own docstring) — replaces whatever was cached before,
                same as every other field here.
            support_phone_secondary: Optional secondary support phone.
            support_whatsapp: Optional WhatsApp contact number.
            support_email: Optional support email address.
            support_hours: Optional free-text support hours.
            support_message: Optional free-text message.

        Returns:
            The saved row.
        """
        row = self.get()
        if row is None:
            row = ClientSubscriptionState(
                status=status,
                company_name=company_name,
                subscription_end_date=subscription_end_date,
                max_devices=max_devices,
                days_remaining=days_remaining,
                support_phone_primary=support_phone_primary,
                support_phone_secondary=support_phone_secondary,
                support_whatsapp=support_whatsapp,
                support_email=support_email,
                support_hours=support_hours,
                support_message=support_message,
                checked_at=_utc_now(),
            )
            self.session.add(row)
        else:
            row.status = status
            row.company_name = company_name
            row.subscription_end_date = subscription_end_date
            row.max_devices = max_devices
            row.days_remaining = days_remaining
            row.support_phone_primary = support_phone_primary
            row.support_phone_secondary = support_phone_secondary
            row.support_whatsapp = support_whatsapp
            row.support_email = support_email
            row.support_hours = support_hours
            row.support_message = support_message
            row.checked_at = _utc_now()
        self.session.flush()
        return row

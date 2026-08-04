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
    ) -> ClientSubscriptionState:
        """Create or overwrite the singleton row with a freshly server-confirmed status.

        Args:
            status: The status the server just reported.
            company_name: The subscription's company name, as reported.
            subscription_end_date: The subscription's end date, as reported.
            max_devices: The subscription's device cap, as reported.
            days_remaining: Days remaining until expiry, as reported.

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
                checked_at=_utc_now(),
            )
            self.session.add(row)
        else:
            row.status = status
            row.company_name = company_name
            row.subscription_end_date = subscription_end_date
            row.max_devices = max_devices
            row.days_remaining = days_remaining
            row.checked_at = _utc_now()
        self.session.flush()
        return row

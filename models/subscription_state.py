"""Local subscription-check bookkeeping for the Attendance Client's own installation.

Mirrors :mod:`models.sync_state`'s singleton-row shape exactly, for the
same reason: an infrastructure-primitive table, extending
:class:`~models.base.Base` directly rather than
:class:`~models.base.BaseModel`, since this is not company-scoped
business data.

:class:`ClientSubscriptionState` caches the last subscription status
the Attendance Server actually confirmed, plus when that confirmation
happened — the basis for :class:`~services.subscription_check_service.SubscriptionCheckService`'s
grace period: if the server cannot be reached at startup, this cached
row (not a locally-stored license file) is what lets the installation
keep running for a bounded window before it must block.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, UTCDateTime


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class ClientSubscriptionState(Base):
    """This installation's last server-confirmed subscription status.

    Exactly one row ever exists.

    Attributes:
        status: The last status the Attendance Server actually
            reported (``"active"``, ``"suspended"``, ``"expired"``, or
            ``"not_linked"`` — see
            :class:`~sync.client.SubscriptionStatusResult`). Never
            written to speculatively; only overwritten by an actual
            successful server response.
        company_name: The subscription's company name, as last
            reported.
        subscription_end_date: The subscription's end date, as last
            reported.
        max_devices: The subscription's device cap, as last reported.
        days_remaining: Days remaining until expiry, as last reported.
        checked_at: When this row was last refreshed by a successful
            server response — the grace period's baseline.
    """

    __tablename__ = "client_subscription_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    subscription_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    max_devices: Mapped[int | None] = mapped_column(Integer, nullable=True)
    days_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utc_now)

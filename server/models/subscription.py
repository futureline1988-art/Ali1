"""Company subscriptions: the server-managed replacement for file-based licensing.

Previously, whether an Attendance Client installation was allowed to
run at all was decided by a locally-verified, Ed25519-signed license
key file (see the retired ``licensing.license_key``/``licensing.license_service``
modules) -- fully offline, but with no way for the vendor to see or
change a customer's status once a key was handed over except by
issuing a new one. :class:`Subscription` replaces that entirely: the
Attendance Server is now the single source of truth for whether a
company is entitled to run the product, checked live at every client
login/startup (see :mod:`server.api.routers.subscriptions`'s
``GET /api/v1/subscription/status``) rather than baked into a file the
client alone can verify.

A subscription is identified to its own Attendance Client
installations by :attr:`Subscription.company_code` (unique,
system-generated at creation time -- see
:func:`~server.services.subscription_service.generate_company_code`)
rather than :attr:`company_name` itself: this server is multi-tenant,
so the Attendance Client must never enumerate or display other
companies, and a company name is guessable/typeable by anyone, while a
code (e.g. ``FUTURELINE-7X4K9P``) is opaque and handed out by the
administrator only to that company's own employees. An Attendance
Client installation's first-ever login is what supplies this code (see
:class:`~server.models.device.SyncDevice.subscription_id`, set once at
registration and resolved by looking up this table by
``company_code``).

:attr:`Subscription.status` is vendor-controlled (Active/Suspended,
set explicitly by the Developer Suite) and deliberately does not
include "Expired" as a storable state -- expiry is *computed* from
:attr:`subscription_end_date` via :attr:`is_expired`, exactly the
:class:`~developer_suite.models.license.IssuedLicense` (retired
alongside the rest of the old licensing system) `is_expired`/`is_active`
split already established in this codebase, so nobody has to remember
to flip a stale flag when a subscription's end date quietly passes.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from sqlalchemy import Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import enum_column_type
from server.database.base import ServerBaseModel


class SubscriptionStatus(str, Enum):
    """A subscription's vendor-controlled state.

    Deliberately excludes "expired" -- see :attr:`Subscription.is_expired`.
    """

    ACTIVE = "active"
    SUSPENDED = "suspended"


class Subscription(ServerBaseModel):
    """One company's subscription to the Attendance Management System.

    Attributes:
        company_name: The paying company's name; globally unique.
            Display-only from the Attendance Client's point of view
            (shown back to it after a successful
            ``company_code``-based registration) -- never used to look
            a subscription up from an untrusted request; see
            :attr:`company_code` for that.
        company_code: A unique, system-generated, opaque code (e.g.
            ``FUTURELINE-7X4K9P`` -- see
            :func:`~server.services.subscription_service.generate_company_code`)
            an Attendance Client installation's administrator types
            once, instead of picking this company's name off any kind
            of list, to link that installation to this subscription
            (see :class:`~server.models.device.SyncDevice`). The
            Developer Suite generates and displays it at subscription
            -creation time, for the vendor to hand to that company's
            administrator out of band.
        subscription_start_date: When this subscription grant began.
        subscription_end_date: When this subscription grant stops
            being valid; past this date :attr:`is_expired` is ``True``
            regardless of :attr:`status`.
        status: Vendor-controlled Active/Suspended state (see
            :class:`SubscriptionStatus`). Set by the Developer Suite via
            :meth:`~server.services.subscription_service.SubscriptionService.suspend`/
            :meth:`~server.services.subscription_service.SubscriptionService.reactivate`.
        max_devices: Maximum number of Attendance Client installations
            (:class:`~server.models.device.SyncDevice` rows with this
            subscription) allowed to register at once. Enforced at
            registration time (see
            :meth:`~server.services.subscription_service.SubscriptionService.check_device_capacity`),
            not retroactively against already-registered devices.
        max_users: Maximum number of user accounts this subscription
            entitles the company to, across all its installations.
            ``None`` means unlimited -- unlike :attr:`max_devices`,
            explicitly optional per this feature's requirements. Not
            enforced by the Attendance Server itself (a "user" is an
            Attendance Client-local concept, see ``models.user.User``,
            entirely outside this server's own schema) -- surfaced to
            the Attendance Client's own user-creation flow as a limit
            to check locally, and to the Developer Suite for display.
    """

    company_name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    company_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    subscription_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    subscription_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[SubscriptionStatus] = mapped_column(
        enum_column_type(SubscriptionStatus), nullable=False, default=SubscriptionStatus.ACTIVE
    )
    max_devices: Mapped[int] = mapped_column(Integer, nullable=False)
    max_users: Mapped[int | None] = mapped_column(Integer, nullable=True)

    @property
    def is_expired(self) -> bool:
        """Whether :attr:`subscription_end_date` has passed as of today."""
        return date.today() > self.subscription_end_date

    @property
    def is_active(self) -> bool:
        """Whether this subscription currently grants access: not suspended, not expired."""
        return self.status is SubscriptionStatus.ACTIVE and not self.is_expired

    @property
    def days_remaining(self) -> int:
        """Days until :attr:`subscription_end_date`; negative once expired."""
        return (self.subscription_end_date - date.today()).days

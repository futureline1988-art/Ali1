"""Create, renew, suspend, reactivate, and query company subscriptions.

The Developer Suite is the only caller of every write method here (see
:mod:`server.api.routers.subscriptions`'s admin-scoped endpoints) — the
server-side counterpart to the retired ``developer_suite.services.license_service.LicenseService``,
minus any cryptography: a subscription's validity is a plain database
row the server itself evaluates, not a signed artifact a client
verifies offline.
"""

from __future__ import annotations

import re
import secrets
from datetime import date

from database.database import Database
from server.models.subscription import Subscription, SubscriptionStatus
from server.repositories.subscription_repository import SubscriptionRepository
from server.services.base_service import BaseService

#: Excludes visually ambiguous characters (0/O, 1/I) -- a company code is
#: read aloud or retyped by hand, unlike most other identifiers in this
#: system.
_COMPANY_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_COMPANY_CODE_SUFFIX_LENGTH = 6
_COMPANY_CODE_PREFIX_MAX_LENGTH = 12
_COMPANY_CODE_GENERATION_ATTEMPTS = 20


class SubscriptionServiceError(Exception):
    """Base class for subscription operation failures the API layer should translate."""


class SubscriptionNotFoundError(SubscriptionServiceError):
    """No subscription exists with the given id or company name."""


class DuplicateCompanyNameError(SubscriptionServiceError):
    """A subscription already exists for this company name."""


class CompanyCodeGenerationFailedError(SubscriptionServiceError):
    """Could not generate a unique company code after several attempts (astronomically unlikely)."""


def generate_company_code(company_name: str) -> str:
    """Build one candidate company code for ``company_name`` (e.g. ``FUTURELINE-7X4K9P``).

    Not itself guaranteed unique -- see :meth:`SubscriptionService.create`,
    which retries this on collision. Deterministic prefix (so the code
    stays recognizable to the vendor) plus a random, unambiguous
    -alphabet suffix (so it cannot be guessed or brute-forced from the
    company name alone).
    """
    slug = re.sub(r"[^A-Za-z0-9]", "", company_name).upper()
    prefix = (slug or "COMPANY")[:_COMPANY_CODE_PREFIX_MAX_LENGTH]
    suffix = "".join(secrets.choice(_COMPANY_CODE_ALPHABET) for _ in range(_COMPANY_CODE_SUFFIX_LENGTH))
    return f"{prefix}-{suffix}"


class SubscriptionService(BaseService):
    """Create, renew, suspend, reactivate, and query subscriptions."""

    def __init__(self, database: Database) -> None:
        """Create a subscription service bound to ``database``."""
        super().__init__(database)

    def create(
        self,
        *,
        company_name: str,
        subscription_start_date: date,
        subscription_end_date: date,
        max_devices: int,
        max_users: int | None = None,
    ) -> Subscription:
        """Create a new subscription.

        Args:
            company_name: The paying company's name; must be unique.
            subscription_start_date: When the grant begins.
            subscription_end_date: When the grant stops being valid.
            max_devices: Maximum concurrently-registered Attendance
                Client installations.
            max_users: Maximum user accounts across the company's
                installations; ``None`` for unlimited.

        Returns:
            The newly created subscription, with a fresh, unique
            :attr:`~server.models.subscription.Subscription.company_code`
            generated automatically -- the Developer Suite displays it
            immediately so the vendor can hand it to the company's
            administrator; it is never entered manually.

        Raises:
            DuplicateCompanyNameError: A subscription already exists
                for ``company_name``.
            CompanyCodeGenerationFailedError: A unique code could not
                be generated (should not happen in practice).
        """
        with self._session_scope() as session:
            repo = SubscriptionRepository(session)
            if repo.get_by_company_name(company_name) is not None:
                raise DuplicateCompanyNameError(
                    f"A subscription already exists for company {company_name!r}."
                )
            company_code = None
            for _ in range(_COMPANY_CODE_GENERATION_ATTEMPTS):
                candidate = generate_company_code(company_name)
                if repo.get_by_company_code(candidate) is None:
                    company_code = candidate
                    break
            if company_code is None:
                raise CompanyCodeGenerationFailedError(
                    "Could not generate a unique company code; please try again."
                )
            subscription = Subscription(
                company_name=company_name,
                company_code=company_code,
                subscription_start_date=subscription_start_date,
                subscription_end_date=subscription_end_date,
                status=SubscriptionStatus.ACTIVE,
                max_devices=max_devices,
                max_users=max_users,
            )
            return repo.add(subscription)

    def renew(self, subscription_id: int, *, new_end_date: date) -> Subscription:
        """Extend a subscription's end date, without changing its status.

        A suspended subscription stays suspended after renewal — renewal
        only moves the expiry date; reactivating a suspended
        subscription is a separate, explicit action (see
        :meth:`reactivate`).

        Args:
            subscription_id: The subscription to renew.
            new_end_date: The new :attr:`~server.models.subscription.Subscription.subscription_end_date`.

        Returns:
            The updated subscription.

        Raises:
            SubscriptionNotFoundError: No subscription with that id.
        """
        return self._update(subscription_id, subscription_end_date=new_end_date)

    def suspend(self, subscription_id: int) -> Subscription:
        """Mark a subscription :attr:`~server.models.subscription.SubscriptionStatus.SUSPENDED`.

        Raises:
            SubscriptionNotFoundError: No subscription with that id.
        """
        return self._update(subscription_id, status=SubscriptionStatus.SUSPENDED)

    def reactivate(self, subscription_id: int) -> Subscription:
        """Mark a subscription :attr:`~server.models.subscription.SubscriptionStatus.ACTIVE`.

        Does not change :attr:`~server.models.subscription.Subscription.subscription_end_date`
        — reactivating an already-expired subscription still leaves it
        expired; use :meth:`renew` too if the intent is to extend it.

        Raises:
            SubscriptionNotFoundError: No subscription with that id.
        """
        return self._update(subscription_id, status=SubscriptionStatus.ACTIVE)

    def update_limits(
        self, subscription_id: int, *, max_devices: int | None = None, max_users: int | None = ...
    ) -> Subscription:
        """Update a subscription's device/user caps.

        Args:
            subscription_id: The subscription to update.
            max_devices: New device cap, or ``None`` to leave unchanged.
            max_users: New user cap (``None`` means "set to unlimited");
                left unchanged if this argument is omitted entirely
                (the ``...`` sentinel default distinguishes "not
                provided" from "explicitly set to unlimited").

        Raises:
            SubscriptionNotFoundError: No subscription with that id.
        """
        fields: dict[str, object] = {}
        if max_devices is not None:
            fields["max_devices"] = max_devices
        if max_users is not ...:
            fields["max_users"] = max_users
        return self._update(subscription_id, **fields)

    def _update(self, subscription_id: int, **fields: object) -> Subscription:
        with self._session_scope() as session:
            repo = SubscriptionRepository(session)
            subscription = repo.get_by_id(subscription_id)
            if subscription is None:
                raise SubscriptionNotFoundError(f"No subscription with id={subscription_id!r}.")
            for field_name, value in fields.items():
                setattr(subscription, field_name, value)
            session.flush()
            return subscription

    def get(self, subscription_id: int) -> Subscription | None:
        """Fetch a single subscription by id, or ``None`` if not found."""
        with self._session_scope() as session:
            return SubscriptionRepository(session).get_by_id(subscription_id)

    def get_by_company_name(self, company_name: str) -> Subscription | None:
        """Fetch a single subscription by company name, or ``None`` if not found."""
        with self._session_scope() as session:
            return SubscriptionRepository(session).get_by_company_name(company_name)

    def get_by_company_code(self, company_code: str) -> Subscription | None:
        """Fetch a single subscription by company code, or ``None`` if not found."""
        with self._session_scope() as session:
            return SubscriptionRepository(session).get_by_company_code(company_code)

    def list_all(self) -> list[Subscription]:
        """List every non-deleted subscription."""
        with self._session_scope() as session:
            return SubscriptionRepository(session).list_all()

    def device_count(self, subscription_id: int) -> int:
        """How many active Attendance Client devices currently count against the cap."""
        with self._session_scope() as session:
            return SubscriptionRepository(session).count_active_devices(subscription_id)

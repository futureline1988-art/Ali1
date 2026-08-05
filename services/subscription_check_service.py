"""Validates this installation's subscription with the Attendance Server at login.

The server-managed replacement for the retired file-based license
system (see :mod:`licensing`, being removed): rather than verifying a
locally-stored, signed license file, this service asks the Attendance
Server directly, via :meth:`~sync.coordinator.ClientSyncCoordinator.get_subscription_status`,
and caches the last *server-confirmed* answer locally (see
:mod:`models.subscription_state`) purely so a temporarily unreachable
server does not lock a paying customer out.

Also owns this installation's fully-automatic, login-driven first-time
enrollment (see :meth:`check_for_login`/:meth:`_auto_enroll`): a fresh
installation has no way to know its own company in advance in a
multi-tenant, central-server deployment, so no company is ever
preconfigured on the client. Instead, the very first successful local
username/password login (see :mod:`ui.login_window` /
:class:`main.ApplicationController`) is what tells this installation
which company it belongs to — :meth:`check_for_login` self-registers
this device against that company's subscription right then, with no
admin bearer token and no manual linking anywhere in the Developer
Suite, and permanently binds this device to that company (see
:meth:`~repositories.sync_repository.ClientSyncCredentialRepository.set_bound_company`)
so every future login skips this step entirely.

Grace period: if the server cannot be reached, this installation is
allowed to keep running for a bounded window (:data:`DEFAULT_GRACE_PERIOD`,
7 days) since the last successful check. The moment the server answers
again, its answer is authoritative immediately -- an explicit
suspended or expired verdict always applies right away, regardless of
any grace period remaining. Grace only ever covers "the server could
not be reached," never "the server said no."
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from database.database import Database
from models.subscription_state import ClientSubscriptionState
from repositories.subscription_state_repository import ClientSubscriptionStateRepository
from repositories.sync_repository import ClientSyncCredentialRepository
from sync.client import (
    DeviceRegistrationRejectedError,
    MaxDevicesReachedError,
    SubscriptionStatusResult,
    SyncClientError,
)
from sync.coordinator import ClientSyncCoordinator, DeviceNotEnrolledError

#: How long this installation may keep running on a cached, previously
#: server-confirmed subscription status after the server becomes
#: unreachable, before it must block.
DEFAULT_GRACE_PERIOD = timedelta(days=7)


class SubscriptionCheckOutcome(str, Enum):
    """Why :meth:`SubscriptionCheckService.check` reached its verdict."""

    VALID = "valid"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    NOT_REGISTERED = "not_registered"
    MAX_DEVICES_REACHED = "max_devices_reached"
    NO_SUBSCRIPTION_FOR_COMPANY = "no_subscription_for_company"
    UNREACHABLE_WITHIN_GRACE = "unreachable_within_grace"
    UNREACHABLE_BLOCKED = "unreachable_blocked"


@dataclass(frozen=True)
class SubscriptionCheckResult:
    """The outcome of one :meth:`SubscriptionCheckService.check` call.

    Attributes:
        outcome: The specific reason behind :attr:`allowed`.
        allowed: Whether this installation may proceed to the rest of
            startup.
        message_ar: A clear, user-facing Arabic message — this
            application's primary UI language.
        message_en: The same message in English, for logs.
        company_name: The subscription's company name, if known.
        days_remaining: Days remaining until expiry, if known.
    """

    outcome: SubscriptionCheckOutcome
    allowed: bool
    message_ar: str
    message_en: str
    company_name: str | None = None
    days_remaining: int | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware_utc(value: datetime) -> datetime:
    """Treat a naive timestamp (e.g. read back from SQLite) as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class SubscriptionCheckService:
    """Checks this installation's subscription status, with a bounded offline grace period."""

    def __init__(
        self,
        database: Database,
        sync_coordinator: ClientSyncCoordinator,
        *,
        device_name: str = "Attendance Client",
        grace_period: timedelta = DEFAULT_GRACE_PERIOD,
    ) -> None:
        """Create a check service bound to ``database`` and ``sync_coordinator``.

        Args:
            database: The Attendance Client's own database, where the
                last server-confirmed status is cached.
            sync_coordinator: Sync coordinator, enrolled or not — if
                not yet enrolled, :meth:`check_for_login` enrolls it
                automatically (see :meth:`_auto_enroll`).
            device_name: A human-readable label for this installation,
                used only for automatic first-time enrollment.
            grace_period: How long a cached, previously-confirmed
                status remains valid once the server becomes
                unreachable.
        """
        self._database = database
        self._sync_coordinator = sync_coordinator
        self._device_name = device_name
        self._grace_period = grace_period

    def get_cached(self) -> ClientSubscriptionState | None:
        """Return the last server-confirmed subscription state, without contacting the server.

        For display purposes only (e.g.
        :class:`~ui.subscription_info_window.SubscriptionInfoWindow`'s
        detail fields) — :meth:`check` is what actually re-verifies.
        """
        with self._database.session_scope() as session:
            return ClientSubscriptionStateRepository(session).get()

    def check(self) -> SubscriptionCheckResult:
        """Check this installation's already-established subscription status.

        Assumes this device has already enrolled — if not, returns
        :attr:`SubscriptionCheckOutcome.NOT_REGISTERED` immediately
        without contacting the server (see :meth:`check_for_login` for
        the first-time, login-driven enrollment step). Otherwise always
        reaches out to the server first; only falls back to the
        cached, last-confirmed status (subject to the grace period) if
        the server cannot be reached at all.
        """
        if not self._sync_coordinator.is_enrolled():
            return SubscriptionCheckResult(
                outcome=SubscriptionCheckOutcome.NOT_REGISTERED,
                allowed=False,
                message_ar="لم يتم تسجيل هذا الجهاز بعد. يرجى تسجيل الدخول أولاً لإتمام التسجيل التلقائي.",
                message_en=(
                    "This installation has not registered yet. Please log in first to "
                    "complete automatic registration."
                ),
            )

        try:
            status = self._sync_coordinator.get_subscription_status()
        except (SyncClientError, DeviceNotEnrolledError):
            return self._evaluate_grace()

        with self._database.session_scope() as session:
            ClientSubscriptionStateRepository(session).save(
                status=status.status,
                company_name=status.company_name,
                subscription_end_date=status.subscription_end_date,
                max_devices=status.max_devices,
                days_remaining=status.days_remaining,
            )

        return self._result_from_live_status(status)

    def check_for_login(self, *, company_id: int, company_name: str) -> SubscriptionCheckResult:
        """Check (and, the first time, perform) this device's enrollment for a just-authenticated login.

        This is what actually decides whether a user who just typed a
        correct local username/password may proceed past the login
        screen (see :mod:`ui.login_window` / ``main.ApplicationController``)
        — a new device cannot be expected to already know its company
        in a multi-tenant, central-server deployment, so no company is
        ever preconfigured on the client; the authenticated login
        itself is what establishes it.

        If this device has never been bound to a company before (a
        brand-new installation, or one whose previous enrollment
        attempt failed — e.g. no subscription existed yet for this
        company), self-registers this device to ``company_name``'s
        active subscription now. On success, this device is
        permanently bound to ``company_id`` (see
        :meth:`~repositories.sync_repository.ClientSyncCredentialRepository.set_bound_company`)
        — every future call to this method, or to :meth:`check`,
        assumes that company from then on; the login screen itself
        stops offering a company choice too (see
        :meth:`~ui.login_window.LoginWindow._populate_companies`).

        If this device is already bound to a company, ``company_id``/
        ``company_name`` are ignored and this is equivalent to
        :meth:`check`.

        Args:
            company_id: The local :class:`~models.company.Company` id
                the user just authenticated into.
            company_name: That company's display name — the exact
                ``Subscription.company_name`` to register against, the
                first time only.
        """
        with self._database.session_scope() as session:
            already_bound = ClientSyncCredentialRepository(session).get_bound_company_id() is not None
        if already_bound:
            return self.check()

        if not company_name:
            return SubscriptionCheckResult(
                outcome=SubscriptionCheckOutcome.NOT_REGISTERED,
                allowed=False,
                message_ar="تعذر تحديد الشركة لهذا الحساب؛ يتعذر التسجيل التلقائي.",
                message_en="Could not determine this account's company; automatic registration cannot proceed.",
            )

        if not self._sync_coordinator.is_enrolled():
            enrollment_failure = self._auto_enroll(company_name)
            if enrollment_failure is not None:
                return enrollment_failure
            with self._database.session_scope() as session:
                ClientSyncCredentialRepository(session).set_bound_company(company_id)

        return self.check()

    def _auto_enroll(self, company_name: str) -> SubscriptionCheckResult | None:
        """Attempt fully-automatic self-registration to ``company_name``'s subscription.

        Returns:
            ``None`` on success (the caller falls through to a live
            status check); a blocking :class:`SubscriptionCheckResult`
            explaining why not, otherwise.
        """
        try:
            self._sync_coordinator.self_enroll(name=self._device_name, company_name=company_name)
            return None
        except MaxDevicesReachedError:
            return SubscriptionCheckResult(
                outcome=SubscriptionCheckOutcome.MAX_DEVICES_REACHED,
                allowed=False,
                message_ar="تم الوصول إلى الحد الأقصى للأجهزة المسموح بها. (Maximum allowed devices reached.)",
                message_en="Maximum allowed devices reached.",
                company_name=company_name,
            )
        except DeviceRegistrationRejectedError:
            return SubscriptionCheckResult(
                outcome=SubscriptionCheckOutcome.NO_SUBSCRIPTION_FOR_COMPANY,
                allowed=False,
                message_ar=(
                    "لا يوجد اشتراك نشط لهذه الشركة على الخادم. يرجى التواصل مع مزوّد الخدمة."
                ),
                message_en="No active subscription exists for this company. Please contact your provider.",
                company_name=company_name,
            )
        except SyncClientError:
            return SubscriptionCheckResult(
                outcome=SubscriptionCheckOutcome.UNREACHABLE_BLOCKED,
                allowed=False,
                message_ar="تعذر الاتصال بخادم الحضور لتسجيل هذا الجهاز تلقائيًا.",
                message_en="Could not reach the Attendance Server to automatically register this installation.",
                company_name=company_name,
            )

    def _result_from_live_status(self, status: SubscriptionStatusResult) -> SubscriptionCheckResult:
        if status.status == "active":
            return SubscriptionCheckResult(
                outcome=SubscriptionCheckOutcome.VALID,
                allowed=True,
                message_ar="الاشتراك فعّال.",
                message_en="Subscription is active.",
                company_name=status.company_name,
                days_remaining=status.days_remaining,
            )
        if status.status == "suspended":
            return SubscriptionCheckResult(
                outcome=SubscriptionCheckOutcome.SUSPENDED,
                allowed=False,
                message_ar="تم إيقاف اشتراك الشركة. يرجى التواصل مع مزوّد الخدمة.",
                message_en="Company subscription is suspended. Please contact your provider.",
                company_name=status.company_name,
            )
        if status.status == "expired":
            return SubscriptionCheckResult(
                outcome=SubscriptionCheckOutcome.EXPIRED,
                allowed=False,
                message_ar="انتهت صلاحية الاشتراك. يرجى تجديد الاشتراك للمتابعة.",
                message_en="Subscription has expired. Please renew to continue.",
                company_name=status.company_name,
            )
        # status.status == "not_linked"
        return SubscriptionCheckResult(
            outcome=SubscriptionCheckOutcome.NOT_REGISTERED,
            allowed=False,
            message_ar="هذا الجهاز غير مرتبط باشتراك شركة على الخادم.",
            message_en="This installation is not linked to a company subscription on the server.",
        )

    def _evaluate_grace(self) -> SubscriptionCheckResult:
        """Fall back to the cached, last-confirmed status while the server is unreachable."""
        with self._database.session_scope() as session:
            cached = ClientSubscriptionStateRepository(session).get()

        if cached is None:
            return SubscriptionCheckResult(
                outcome=SubscriptionCheckOutcome.UNREACHABLE_BLOCKED,
                allowed=False,
                message_ar=(
                    "تعذر الاتصال بخادم الحضور للتحقق من الاشتراك، "
                    "ولا توجد بيانات اشتراك محفوظة من تحقق سابق."
                ),
                message_en=(
                    "Could not reach the Attendance Server to verify the subscription, "
                    "and no previously confirmed subscription is cached."
                ),
            )

        # An explicit suspended/expired/not-linked verdict always applies
        # immediately, cached or not -- grace only ever covers "the
        # server could not be reached," never "the server said no."
        if cached.status == "suspended":
            return SubscriptionCheckResult(
                outcome=SubscriptionCheckOutcome.SUSPENDED,
                allowed=False,
                message_ar="تم إيقاف اشتراك الشركة. يرجى التواصل مع مزوّد الخدمة.",
                message_en="Company subscription is suspended. Please contact your provider.",
                company_name=cached.company_name,
            )
        if cached.status == "expired":
            return SubscriptionCheckResult(
                outcome=SubscriptionCheckOutcome.EXPIRED,
                allowed=False,
                message_ar="انتهت صلاحية الاشتراك. يرجى تجديد الاشتراك للمتابعة.",
                message_en="Subscription has expired. Please renew to continue.",
                company_name=cached.company_name,
            )
        if cached.status == "not_linked":
            return SubscriptionCheckResult(
                outcome=SubscriptionCheckOutcome.NOT_REGISTERED,
                allowed=False,
                message_ar="هذا الجهاز غير مرتبط باشتراك شركة على الخادم.",
                message_en="This installation is not linked to a company subscription on the server.",
            )

        elapsed = _utc_now() - _as_aware_utc(cached.checked_at)
        if elapsed <= self._grace_period:
            remaining_days = (self._grace_period - elapsed).days
            return SubscriptionCheckResult(
                outcome=SubscriptionCheckOutcome.UNREACHABLE_WITHIN_GRACE,
                allowed=True,
                message_ar=(
                    "تعذر الاتصال بخادم الحضور للتحقق من الاشتراك. "
                    f"تم السماح بالدخول مؤقتًا (يتبقى من فترة السماح: {remaining_days} يوم)."
                ),
                message_en=(
                    "Could not reach the Attendance Server to verify the subscription. "
                    f"Temporary access granted ({remaining_days} day(s) of grace period remaining)."
                ),
                company_name=cached.company_name,
            )

        return SubscriptionCheckResult(
            outcome=SubscriptionCheckOutcome.UNREACHABLE_BLOCKED,
            allowed=False,
            message_ar="تعذر الاتصال بخادم الحضور للتحقق من الاشتراك، وانتهت فترة السماح المؤقتة.",
            message_en=(
                "Could not reach the Attendance Server to verify the subscription, "
                "and the temporary grace period has expired."
            ),
            company_name=cached.company_name,
        )

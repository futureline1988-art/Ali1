"""Validates this installation's subscription with the Attendance Server at startup/login.

The server-managed replacement for the retired file-based license
system (see :mod:`licensing`, being removed): rather than verifying a
locally-stored, signed license file, this service asks the Attendance
Server directly, via :meth:`~sync.coordinator.ClientSyncCoordinator.get_subscription_status`,
and caches the last *server-confirmed* answer locally (see
:mod:`models.subscription_state`) purely so a temporarily unreachable
server does not lock a paying customer out.

Also owns this installation's fully-automatic first-run enrollment
(see :meth:`SubscriptionCheckService._auto_enroll`): if not yet
enrolled, :meth:`check` self-registers with only a configured
``company_name`` — no admin bearer token, no manual linking anywhere
in the Developer Suite. A freshly-registered installation falls
straight through to a live status check in the same call, so "register
-> allowed" is one seamless step from the caller's point of view.

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
        company_name: str = "",
        device_name: str = "Attendance Client",
        grace_period: timedelta = DEFAULT_GRACE_PERIOD,
    ) -> None:
        """Create a check service bound to ``database`` and ``sync_coordinator``.

        Args:
            database: The Attendance Client's own database, where the
                last server-confirmed status is cached.
            sync_coordinator: Sync coordinator, enrolled or not — if
                not yet enrolled, :meth:`check` enrolls it
                automatically (see :meth:`_auto_enroll`).
            company_name: This installation's configured company name
                (``config.sync.company_name``), used only for
                automatic first-run enrollment. Left empty, a
                not-yet-enrolled installation simply reports
                :attr:`SubscriptionCheckOutcome.NOT_REGISTERED` without
                contacting the server.
            device_name: A human-readable label for this installation,
                used only for automatic first-run enrollment.
            grace_period: How long a cached, previously-confirmed
                status remains valid once the server becomes
                unreachable.
        """
        self._database = database
        self._sync_coordinator = sync_coordinator
        self._company_name = company_name
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
        """Check this installation's subscription status.

        If not yet enrolled, first attempts fully-automatic
        self-registration (see :meth:`_auto_enroll`) — on success,
        falls straight through to a live status check below in the
        same call. Otherwise always reaches out to the server first;
        only falls back to the cached, last-confirmed status (subject
        to the grace period) if the server cannot be reached at all.
        """
        if not self._sync_coordinator.is_enrolled():
            enrollment_failure = self._auto_enroll()
            if enrollment_failure is not None:
                return enrollment_failure

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

    def _auto_enroll(self) -> SubscriptionCheckResult | None:
        """Attempt fully-automatic first-run self-registration.

        Returns:
            ``None`` on success (the caller falls through to a live
            status check); a blocking :class:`SubscriptionCheckResult`
            explaining why not, otherwise.
        """
        if not self._company_name:
            return SubscriptionCheckResult(
                outcome=SubscriptionCheckOutcome.NOT_REGISTERED,
                allowed=False,
                message_ar=(
                    "لم يتم إعداد اسم الشركة لهذا الجهاز؛ يتعذر التسجيل التلقائي. "
                    "يرجى التحقق من إعدادات التثبيت."
                ),
                message_en=(
                    "No company name is configured for this installation; automatic "
                    "registration cannot proceed. Please check the installation settings."
                ),
            )
        try:
            self._sync_coordinator.self_enroll(name=self._device_name, company_name=self._company_name)
            return None
        except MaxDevicesReachedError:
            return SubscriptionCheckResult(
                outcome=SubscriptionCheckOutcome.MAX_DEVICES_REACHED,
                allowed=False,
                message_ar="تم الوصول إلى الحد الأقصى للأجهزة المسموح بها. (Maximum allowed devices reached.)",
                message_en="Maximum allowed devices reached.",
                company_name=self._company_name,
            )
        except DeviceRegistrationRejectedError:
            return SubscriptionCheckResult(
                outcome=SubscriptionCheckOutcome.NO_SUBSCRIPTION_FOR_COMPANY,
                allowed=False,
                message_ar=(
                    "لا يوجد اشتراك نشط لهذه الشركة على الخادم. يرجى التواصل مع مزوّد الخدمة."
                ),
                message_en="No active subscription exists for this company. Please contact your provider.",
                company_name=self._company_name,
            )
        except SyncClientError:
            return SubscriptionCheckResult(
                outcome=SubscriptionCheckOutcome.UNREACHABLE_BLOCKED,
                allowed=False,
                message_ar="تعذر الاتصال بخادم الحضور لتسجيل هذا الجهاز تلقائيًا.",
                message_en="Could not reach the Attendance Server to automatically register this installation.",
                company_name=self._company_name,
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

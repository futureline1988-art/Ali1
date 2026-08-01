"""High-level license API: the one module the rest of the application talks to.

Everything above this module (``ui/license_window.py``, ``main.py``)
only ever calls :class:`LicenseService` — it never touches
:mod:`licensing.license_key`, :mod:`licensing.license_store`, or
:mod:`licensing.machine_id` directly, so those can evolve freely as
long as this class's public methods keep their contract.

Adding an online license server later
--------------------------------------
Verification is routed through the :class:`LicenseBackend` protocol,
which :class:`LocalLicenseBackend` (the only implementation today)
satisfies purely offline via the embedded public key. To add online
verification/activation later - e.g. to check a subscription hasn't
been cancelled, or to enforce a server-side seat count - implement a
second class satisfying the same ``verify(license_key) -> LicensePayload``
contract (raising :class:`~licensing.license_key.LicenseKeyError` on
any rejection, exactly like the local one) and pass it to
``LicenseService(backend=...)``. Nothing in :class:`LicenseService`,
the local store, or the activation window needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from licensing.enums import LicenseStatusCode, LicenseType
from licensing.keys import load_public_key
from licensing.license_key import (
    LicenseKeyError,
    LicensePayload,
    decode_and_verify_license_key,
)
from licensing.license_store import LicenseStore, StoredLicenseRecord, utc_now
from licensing.machine_id import get_machine_id

_DEFAULT_TRIAL_DAYS = 14


class LicenseServiceError(Exception):
    """Base class for license activation failures the UI should display."""


class InvalidLicenseKeyError(LicenseServiceError):
    """The provided key is malformed or its signature does not verify."""


class LicenseMachineMismatchError(LicenseServiceError):
    """The key is locked to a different machine than this one."""


class TrialAlreadyUsedError(LicenseServiceError):
    """A trial has already been started on this machine."""


class LicenseBackend(Protocol):
    """Anything that can turn a license key string into a verified payload."""

    def verify(self, license_key: str) -> LicensePayload:
        """Verify ``license_key`` and return its payload.

        Raises:
            ~licensing.license_key.LicenseKeyError: If the key is
                malformed, its signature does not verify, or (for a
                future networked backend) the server rejects it.
        """
        ...


class LocalLicenseBackend:
    """Verifies a license key entirely offline, using the embedded public key."""

    def __init__(self) -> None:
        """Load the application's embedded public key once."""
        self._public_key = load_public_key()

    def verify(self, license_key: str) -> LicensePayload:
        """Verify ``license_key``'s Ed25519 signature and decode its payload."""
        return decode_and_verify_license_key(license_key, self._public_key)


@dataclass(frozen=True)
class LicenseStatus:
    """The result of checking the currently stored (or absent) license.

    Attributes:
        code: The machine-readable outcome.
        license_type: Which plan is/was active, if any record exists at
            all (even an expired or mismatched one).
        expires_at: When the checked license expires; ``None`` if it
            never expires or no license was found.
        days_remaining: Days until :attr:`expires_at`; ``None`` if it
            never expires or no license was found. Negative if already
            expired.
        message_ar: A ready-to-display Arabic status message.
    """

    code: LicenseStatusCode
    license_type: LicenseType | None
    expires_at: date | None
    days_remaining: int | None
    message_ar: str

    @property
    def is_valid(self) -> bool:
        """Whether the application is licensed to run."""
        return self.code is LicenseStatusCode.VALID


class LicenseService:
    """Activates, checks, and reports on this machine's application license."""

    def __init__(
        self,
        *,
        backend: LicenseBackend | None = None,
        store: LicenseStore | None = None,
    ) -> None:
        """Create a license service.

        Args:
            backend: Verifies vendor-issued keys; defaults to
                :class:`LocalLicenseBackend`. Inject a different
                implementation (e.g. a future networked one) here.
            store: Local encrypted persistence; defaults to a new
                :class:`~licensing.license_store.LicenseStore`.
                Overridable for tests.
        """
        self._backend = backend or LocalLicenseBackend()
        self._store = store or LicenseStore()

    @property
    def machine_id(self) -> str:
        """This machine's fingerprint, for display in the activation window."""
        return get_machine_id()

    def get_status(self) -> LicenseStatus:
        """Check the currently stored license, if any.

        A vendor-issued key is re-verified (signature + machine lock)
        on every call rather than trusted at face value, so a stored
        record that somehow became invalid (e.g. hand-edited) is
        caught here rather than only at activation time.

        Returns:
            The current :class:`LicenseStatus`.
        """
        envelope = self._store.load()
        record = envelope.current
        if record is None:
            return LicenseStatus(
                code=LicenseStatusCode.NOT_ACTIVATED,
                license_type=None,
                expires_at=None,
                days_remaining=None,
                message_ar="لم يتم تفعيل النظام بعد.",
            )

        if record.license_type is LicenseType.TRIAL:
            return self._status_from_expiry(record.license_type, record.expires_at)

        try:
            payload = self._backend.verify(record.raw_key or "")
        except LicenseKeyError:
            return LicenseStatus(
                code=LicenseStatusCode.INVALID,
                license_type=record.license_type,
                expires_at=record.expires_at,
                days_remaining=None,
                message_ar="بيانات الترخيص المخزنة غير صالحة. الرجاء إعادة التفعيل.",
            )

        if payload.machine_id is not None and payload.machine_id != get_machine_id():
            return LicenseStatus(
                code=LicenseStatusCode.MACHINE_MISMATCH,
                license_type=record.license_type,
                expires_at=record.expires_at,
                days_remaining=None,
                message_ar="هذا الترخيص مرتبط بجهاز آخر.",
            )

        return self._status_from_expiry(record.license_type, record.expires_at)

    def _status_from_expiry(
        self, license_type: LicenseType, expires_at: date | None
    ) -> LicenseStatus:
        """Build a VALID/EXPIRED :class:`LicenseStatus` from an expiry date."""
        if expires_at is None:
            return LicenseStatus(
                code=LicenseStatusCode.VALID,
                license_type=license_type,
                expires_at=None,
                days_remaining=None,
                message_ar=f"مفعّل - {license_type.label_ar}.",
            )

        days_remaining = (expires_at - date.today()).days
        if days_remaining < 0:
            return LicenseStatus(
                code=LicenseStatusCode.EXPIRED,
                license_type=license_type,
                expires_at=expires_at,
                days_remaining=days_remaining,
                message_ar=f"انتهت صلاحية الترخيص ({license_type.label_ar}) بتاريخ {expires_at.isoformat()}.",
            )
        return LicenseStatus(
            code=LicenseStatusCode.VALID,
            license_type=license_type,
            expires_at=expires_at,
            days_remaining=days_remaining,
            message_ar=f"مفعّل - {license_type.label_ar} - متبقٍ {days_remaining} يومًا.",
        )

    def activate(self, license_key: str) -> LicenseStatus:
        """Verify and activate a vendor-issued license key on this machine.

        Args:
            license_key: The key string the customer received from the
                vendor.

        Returns:
            The resulting :class:`LicenseStatus` (always ``VALID`` if
            this method returns instead of raising).

        Raises:
            InvalidLicenseKeyError: The key is malformed or its
                signature does not verify.
            LicenseMachineMismatchError: The key is locked to a
                different machine.
        """
        try:
            payload = self._backend.verify(license_key)
        except LicenseKeyError as exc:
            raise InvalidLicenseKeyError(str(exc)) from exc

        current_machine_id = get_machine_id()
        if payload.machine_id is not None and payload.machine_id != current_machine_id:
            raise LicenseMachineMismatchError(
                "This license key is locked to a different machine."
            )

        record = StoredLicenseRecord(
            license_type=payload.license_type,
            raw_key=license_key.strip(),
            machine_id=current_machine_id,
            activated_at=utc_now(),
            expires_at=payload.expires_at,
        )
        self._store.set_current(record)
        return self.get_status()

    def is_trial_available(self) -> bool:
        """Whether :meth:`start_trial` can still be called on this machine.

        Lets the activation window disable the trial option proactively
        instead of only reacting to a :class:`TrialAlreadyUsedError`.

        Returns:
            ``True`` if no trial has ever been started on this machine.
        """
        return not self._store.load().trial_used

    def start_trial(self, *, days: int = _DEFAULT_TRIAL_DAYS) -> LicenseStatus:
        """Start a self-issued trial license on this machine.

        Args:
            days: How many days the trial should last.

        Returns:
            The resulting :class:`LicenseStatus`.

        Raises:
            TrialAlreadyUsedError: A trial was already started on this
                machine before (even if it has since expired or been
                replaced by a paid key).
        """
        if self._store.load().trial_used:
            raise TrialAlreadyUsedError("A trial license has already been used on this machine.")

        record = StoredLicenseRecord(
            license_type=LicenseType.TRIAL,
            raw_key=None,
            machine_id=get_machine_id(),
            activated_at=utc_now(),
            expires_at=date.today() + timedelta(days=days),
        )
        self._store.set_current(record)
        return self.get_status()

    def deactivate(self) -> None:
        """Clear the currently active license (trial eligibility is unaffected)."""
        self._store.clear_current()

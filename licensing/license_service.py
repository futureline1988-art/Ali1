"""High-level license API: the one module the rest of the application talks to.

Everything above this module (``ui/license_window.py``,
``ui/license_info_window.py``, ``main.py``) only ever calls
:class:`LicenseService` — it never touches :mod:`licensing.license_key`,
:mod:`licensing.license_store`, or :mod:`licensing.machine_id` directly,
so those can evolve freely as long as this class's public methods keep
their contract.

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
the local store, or either license window needs to change.

On "preventing" simultaneous activation on two machines
----------------------------------------------------------
:meth:`LicenseService.deactivate` and :meth:`export_transfer_request`
support a *procedural* single-active-machine policy: deactivating
clears this machine's local record immediately, and can produce a
transfer-request file embedding the original vendor-signed key as
verifiable proof of a legitimate prior activation, for the vendor to
review before issuing a replacement key for a different machine. This
is not - and no purely offline mechanism can be - a cryptographic
guarantee that the same key isn't *also* active elsewhere; a real-time
guarantee needs a server tracking active activations, which is exactly
what the :class:`LicenseBackend` extension point above exists to add
later without reshaping this module.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
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
_RENEWABLE_TYPES = (LicenseType.MONTHLY, LicenseType.YEARLY)


class LicenseServiceError(Exception):
    """Base class for license operation failures the UI should display."""


class InvalidLicenseKeyError(LicenseServiceError):
    """The provided key is malformed or its signature does not verify."""


class LicenseMachineMismatchError(LicenseServiceError):
    """The key is locked to a different machine than this one."""


class TrialAlreadyUsedError(LicenseServiceError):
    """A trial has already been started on this machine."""


class NoRenewableLicenseError(LicenseServiceError):
    """There is no currently active Monthly/Yearly license to renew."""


class InvalidRenewalTypeError(LicenseServiceError):
    """The renewal key is not itself a Monthly or Yearly license."""


class NoActiveLicenseError(LicenseServiceError):
    """There is no currently active license to act on."""


class TrialNotTransferableError(LicenseServiceError):
    """A self-issued trial has no vendor-issued key to build a transfer request from."""


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


@dataclass(frozen=True)
class LicenseDetails:
    """Everything the License Information screen needs to display.

    Attributes:
        status: The current license status (type, expiry, message).
        license_id: The vendor-assigned grant identifier; ``None`` for
            a trial or when no license is stored.
        company_name: The organization the license was issued to;
            ``None`` if unset on the key (older keys - see
            :attr:`~licensing.license_key.LicensePayload.company_name`)
            or if no vendor-issued license is stored.
        customer_name: The named contact the license was issued to;
            ``None`` for a trial or when no license is stored.
        activated_at: When the current record was activated on this
            machine; ``None`` if nothing is stored.
        machine_id: This machine's fingerprint (always available).
    """

    status: LicenseStatus
    license_id: str | None
    company_name: str | None
    customer_name: str | None
    activated_at: datetime | None
    machine_id: str


@dataclass(frozen=True)
class TransferRequest:
    """A record of "this license was deactivated here, for transfer elsewhere".

    Its :attr:`original_signed_key` is what makes it independently
    verifiable: it is the exact Ed25519-signed key the vendor issued,
    so the vendor (or anyone holding the public key) can re-verify it
    without trusting the rest of this file's contents - only its
    presence together with a matching :attr:`license_id` is what
    "signs" this request, since the application itself holds no
    private key to sign new documents with.

    Attributes:
        request_id: A unique identifier for this specific request.
        license_id: The license grant this request concerns.
        company_name: The organization on the original license, if set.
        customer_name: The named contact on the original license.
        license_type: The plan being transferred.
        machine_id: The machine this request was generated on (i.e.
            being deactivated *from*).
        activated_at: When the license was activated on this machine.
        expires_at: The license's expiry, if any.
        requested_at: When this transfer request was generated.
        original_signed_key: The original vendor-signed license key
            string, embedded verbatim.
    """

    request_id: str
    license_id: str
    company_name: str | None
    customer_name: str
    license_type: LicenseType
    machine_id: str
    activated_at: datetime
    expires_at: date | None
    requested_at: datetime
    original_signed_key: str

    def to_json_dict(self) -> dict[str, object]:
        """Serialize to a JSON-safe dict, for :meth:`LicenseService.export_transfer_request`."""
        return {
            "request_id": self.request_id,
            "license_id": self.license_id,
            "company_name": self.company_name,
            "customer_name": self.customer_name,
            "license_type": self.license_type.value,
            "machine_id": self.machine_id,
            "activated_at": self.activated_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "requested_at": self.requested_at.isoformat(),
            "original_signed_key": self.original_signed_key,
        }


class LicenseService:
    """Activates, renews, transfers, and reports on this machine's application license."""

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
        """This machine's fingerprint, for display in the license windows."""
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
        record = self._store.load().current
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

    def get_details(self) -> LicenseDetails:
        """Gather everything the License Information screen needs in one call.

        Returns:
            The current :class:`LicenseDetails`. Every optional field
            is ``None`` when there is no stored license, the stored
            license is a trial (self-issued, no vendor payload to read
            ``license_id``/``company_name``/``customer_name`` from), or
            the stored key no longer verifies (mirrors
            :meth:`get_status`'s ``INVALID`` handling instead of
            raising).
        """
        status = self.get_status()
        record = self._store.load().current

        license_id: str | None = None
        company_name: str | None = None
        customer_name: str | None = None

        if record is not None and record.license_type is not LicenseType.TRIAL and record.raw_key:
            try:
                payload = self._backend.verify(record.raw_key)
            except LicenseKeyError:
                pass
            else:
                license_id = payload.license_id
                company_name = payload.company_name
                customer_name = payload.customer_name

        return LicenseDetails(
            status=status,
            license_id=license_id,
            company_name=company_name,
            customer_name=customer_name,
            activated_at=record.activated_at if record is not None else None,
            machine_id=get_machine_id(),
        )

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

    def _verify_and_check_machine(self, license_key: str) -> LicensePayload:
        """Verify a key's signature and, if it is machine-locked, that it matches this one.

        Shared by :meth:`activate` and :meth:`renew` so both apply
        identical checks before touching the store.

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

        if payload.machine_id is not None and payload.machine_id != get_machine_id():
            raise LicenseMachineMismatchError(
                "This license key is locked to a different machine."
            )
        return payload

    def _store_activated(self, payload: LicensePayload, raw_key: str) -> LicenseStatus:
        """Persist a verified payload as the current license and return the fresh status."""
        record = StoredLicenseRecord(
            license_type=payload.license_type,
            raw_key=raw_key.strip(),
            machine_id=get_machine_id(),
            activated_at=utc_now(),
            expires_at=payload.expires_at,
        )
        self._store.set_current(record)
        return self.get_status()

    def activate(self, license_key: str) -> LicenseStatus:
        """Verify and activate a vendor-issued license key on this machine.

        Replaces whatever license (if any) was previously active,
        without touching the database, company settings, or any other
        application state.

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
        payload = self._verify_and_check_machine(license_key)
        return self._store_activated(payload, license_key)

    def renew(self, new_license_key: str) -> LicenseStatus:
        """Renew the current Monthly/Yearly license with a newly issued key.

        Unlike :meth:`activate`, this requires there to already be a
        Monthly or Yearly license on record (active *or* expired -
        renewing an already-expired subscription is the primary use
        case) and requires the new key to itself be Monthly or Yearly;
        switching plan types, or activating from nothing, goes through
        :meth:`activate` instead.

        Args:
            new_license_key: The freshly issued key string.

        Returns:
            The resulting :class:`LicenseStatus`, with the updated
            expiration date immediately reflected.

        Raises:
            NoRenewableLicenseError: There is no current Monthly/Yearly
                license to renew.
            InvalidRenewalTypeError: The new key is not itself Monthly
                or Yearly.
            InvalidLicenseKeyError: The new key is malformed or its
                signature does not verify.
            LicenseMachineMismatchError: The new key is locked to a
                different machine.
        """
        current = self._store.load().current
        if current is None or current.license_type not in _RENEWABLE_TYPES:
            raise NoRenewableLicenseError(
                "Only a current Monthly or Yearly license can be renewed; "
                "use activate() to set up a new license."
            )

        payload = self._verify_and_check_machine(new_license_key)
        if payload.license_type not in _RENEWABLE_TYPES:
            raise InvalidRenewalTypeError(
                "The renewal key must itself be a Monthly or Yearly license."
            )

        return self._store_activated(payload, new_license_key)

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

    def build_transfer_request(self) -> TransferRequest:
        """Build (without exporting) a transfer request from the current license.

        Returns:
            The :class:`TransferRequest`, ready for
            :meth:`export_transfer_request` or direct inspection.

        Raises:
            NoActiveLicenseError: There is no active license to build a
                request from.
            TrialNotTransferableError: The active license is a
                self-issued trial, which has no vendor-issued key to
                embed as proof.
            InvalidLicenseKeyError: The stored key no longer verifies
                (corrupted or otherwise invalid).
        """
        current = self._store.load().current
        if current is None:
            raise NoActiveLicenseError("There is no active license to export a transfer request for.")
        if current.license_type is LicenseType.TRIAL or not current.raw_key:
            raise TrialNotTransferableError("Trial licenses cannot be transferred to another machine.")

        try:
            payload = self._backend.verify(current.raw_key)
        except LicenseKeyError as exc:
            raise InvalidLicenseKeyError(
                f"The stored license key is no longer valid, cannot build a transfer request: {exc}"
            ) from exc

        return TransferRequest(
            request_id=str(uuid.uuid4()),
            license_id=payload.license_id,
            company_name=payload.company_name,
            customer_name=payload.customer_name,
            license_type=current.license_type,
            machine_id=current.machine_id,
            activated_at=current.activated_at,
            expires_at=current.expires_at,
            requested_at=utc_now(),
            original_signed_key=current.raw_key,
        )

    def export_transfer_request(self, output_path: Path) -> TransferRequest:
        """Write a transfer request for the current license to ``output_path``.

        Does not deactivate the license - send the resulting file to
        the vendor, who can then revoke the old activation on their
        side and issue a replacement key for a different machine (see
        this module's docstring for what "revoke" can and cannot mean
        without a license server).

        Args:
            output_path: Where to write the request, as JSON.

        Returns:
            The :class:`TransferRequest` that was written.

        Raises:
            NoActiveLicenseError: There is no active license to export.
            TrialNotTransferableError: The active license is a trial.
            InvalidLicenseKeyError: The stored key no longer verifies.
        """
        request = self.build_transfer_request()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(request.to_json_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return request

    def deactivate(self, *, export_transfer_request_to: Path | None = None) -> TransferRequest | None:
        """Clear the currently active license on this machine.

        Trial eligibility is unaffected - deactivating a trial does not
        grant a new one.

        Args:
            export_transfer_request_to: If given, a transfer request is
                built and written to this path *before* the local
                record is cleared (since building it needs the record
                that is about to be removed). Raises the same errors as
                :meth:`export_transfer_request` if given for a trial or
                when nothing is active; omit it to just deactivate.

        Returns:
            The :class:`TransferRequest` that was written, or ``None``
            if ``export_transfer_request_to`` was not given.
        """
        request: TransferRequest | None = None
        if export_transfer_request_to is not None:
            request = self.export_transfer_request(export_transfer_request_to)
        self._store.clear_current()
        return request

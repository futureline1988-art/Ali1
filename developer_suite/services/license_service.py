"""License issuance, renewal, and revocation business logic.

Deliberately calls straight into the existing, already-shipping
licensing library instead of reimplementing any of it:

* :func:`licensing.license_generator.issue_license_key` performs the
  actual key construction (including expiry-date computation for each
  :class:`~licensing.enums.LicenseType`) and signing — the single
  source of truth this service defers to rather than duplicating.
* :func:`licensing.license_key.decode_and_verify_license_key` is then
  used, against the *same* keypair that just signed it, purely to read
  back the computed ``issued_at``/``expires_at``/``license_id`` for
  this application's own bookkeeping row — a self-consistency check as
  a side benefit, not a second, separate calculation of those values.
* :func:`licensing.crypto.signing.ensure_keypair` loads the vendor's
  private key from wherever this application is configured to keep it
  (see :attr:`~developer_suite.config.DeveloperSuiteConfig.licensing_private_key_path`),
  generating it once, automatically, the first time this application
  is ever asked to issue or renew a license on a machine that doesn't
  have one yet — this is the Developer Suite's own signing key, held
  only here, so "this machine" is by construction always a developer
  /vendor machine, never a customer's Attendance Client installation
  (see :meth:`LicenseService._load_private_key`).

This module intentionally does not touch the Attendance Client's own
license *verification* path (:mod:`licensing.license_service`,
``main.py``'s startup check, or ``licensing/keys.py``'s embedded
public key) — nothing here changes how, or whether, a license key the
Attendance Client already holds is accepted.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from licensing.crypto.signing import SigningKeyError, ensure_keypair
from licensing.enums import LicenseType
from licensing.license_generator import issue_license_key
from licensing.license_key import decode_and_verify_license_key

from database.database import Database
from developer_suite.models.license import IssuedLicense, IssuedLicenseStatus
from developer_suite.repositories.customer_repository import CustomerRepository
from developer_suite.repositories.license_repository import LicenseRepository
from developer_suite.services.base_service import BaseService
from developer_suite.services.customer_service import CustomerNotFoundError


class LicenseServiceError(Exception):
    """Base class for license operation failures the UI should display."""


class LicenseNotFoundError(LicenseServiceError):
    """No issued license exists with the given id."""


class LicenseSigningKeyError(LicenseServiceError):
    """The vendor's signing private key exists but is invalid.

    A *missing* key no longer reaches the UI as an error at all — see
    :meth:`LicenseService._load_private_key` — so this now only fires
    for a key file that is present but corrupt (not valid Ed25519 PEM),
    which genuinely does need a human to look at it rather than being
    silently replaced. Raised instead of letting
    :class:`licensing.crypto.signing.SigningKeyError` propagate
    directly, so the UI layer has one exception type to catch for
    every licensing operation.
    """


class LicenseService(BaseService):
    """Issue, renew, revoke, and search the vendor's issued licenses."""

    def __init__(
        self,
        database: Database,
        *,
        private_key_path: Path,
        public_key_path: Path | None = None,
    ) -> None:
        """Create a license service bound to ``database`` and a signing key location.

        Args:
            database: This application's own database.
            private_key_path: Where to load the vendor's Ed25519
                signing private key from for every issuance/renewal
                (see
                :attr:`~developer_suite.config.DeveloperSuiteConfig.licensing_private_key_path`).
                Auto-created here, once, the first time it's needed if
                nothing exists at this path yet — see
                :func:`licensing.crypto.signing.ensure_keypair`.
            public_key_path: Where to also write the matching public
                key if a new keypair is generated (see
                :attr:`~developer_suite.config.DeveloperSuiteConfig.licensing_public_key_path`)
                — purely so the vendor can retrieve it afterwards and
                embed it in the next Attendance Client build's
                ``licensing/keys.py``. Optional; omit to skip writing
                it out.
        """
        super().__init__(database)
        self._private_key_path = private_key_path
        self._public_key_path = public_key_path

    def _load_private_key(self) -> Ed25519PrivateKey:
        """Load the configured signing private key, generating it once if missing.

        This machine is, by construction, always the vendor's own —
        the private key configured here is never present on (or
        needed by) a customer's Attendance Client installation, which
        only ever holds ``licensing/keys.py``'s embedded *public* key.
        So the first time this is called with nothing on disk yet, a
        fresh keypair is created automatically (see
        :func:`~licensing.crypto.signing.ensure_keypair`) rather than
        surfacing a "please run this CLI command yourself" error a
        packaged, Python-less Windows install has no way to act on.

        An *existing* key is always loaded as-is and never regenerated
        or overwritten, even if the caller wanted a different one — a
        deliberate re-key is a separate, explicit operation, not a
        side effect of clicking "Issue License".

        Raises:
            LicenseSigningKeyError: A key file exists at the
                configured path but does not contain a valid Ed25519
                private key.
        """
        try:
            return ensure_keypair(self._private_key_path, public_key_path=self._public_key_path)
        except SigningKeyError as exc:
            raise LicenseSigningKeyError(str(exc)) from exc

    def _issue_key_for(
        self,
        *,
        customer_name: str,
        company_name: str,
        license_type: LicenseType,
        machine_id: str | None,
        licensed_version: str | None,
        days: int | None,
    ) -> tuple[str, date, date | None]:
        """Sign a new key and read back its computed issue/expiry dates.

        Returns:
            ``(license_key, issued_at, expires_at)``.
        """
        private_key = self._load_private_key()
        license_key = issue_license_key(
            private_key_path=self._private_key_path,
            customer_name=customer_name,
            company_name=company_name,
            license_type=license_type,
            machine_id=machine_id,
            licensed_version=licensed_version,
            days=days,
        )
        payload = decode_and_verify_license_key(license_key, private_key.public_key())
        return license_key, payload.issued_at, payload.expires_at

    def issue_license(
        self,
        *,
        customer_id: int,
        license_type: LicenseType,
        machine_id: str | None = None,
        licensed_version: str | None = None,
        days: int | None = None,
    ) -> IssuedLicense:
        """Issue a new signed license key to a customer.

        Args:
            customer_id: The customer to issue the license to.
            license_type: Which plan to issue.
            machine_id: Lock the key to a specific machine fingerprint;
                ``None`` leaves it unlocked.
            licensed_version: Cap the key to application versions up
                to and including this one; ``None`` leaves it
                unrestricted.
            days: Override the plan's default validity period, in
                days.

        Returns:
            The newly created, active issued-license record.

        Raises:
            CustomerNotFoundError: No customer exists with that id.
            LicenseSigningKeyError: The vendor's signing key exists but
                is invalid (a missing one is auto-created instead —
                see :meth:`_load_private_key`).
        """
        with self._session_scope() as session:
            customer = CustomerRepository(session).get_by_id(customer_id)
            if customer is None:
                raise CustomerNotFoundError(f"No customer with id={customer_id!r}.")

            license_key, issued_at, expires_at = self._issue_key_for(
                customer_name=customer.contact_name,
                company_name=customer.company_name,
                license_type=license_type,
                machine_id=machine_id,
                licensed_version=licensed_version,
                days=days,
            )

            record = IssuedLicense(
                customer_id=customer.id,
                customer=customer,
                license_type=license_type,
                license_key=license_key,
                machine_id=machine_id,
                licensed_version=licensed_version,
                issued_at=issued_at,
                expires_at=expires_at,
                status=IssuedLicenseStatus.ACTIVE,
            )
            LicenseRepository(session).add(record)
            return record

    def renew_license(self, license_id: int, *, days: int | None = None) -> IssuedLicense:
        """Re-issue a license's key with a fresh issue date, extending its expiry.

        Keeps the same customer, plan, machine lock, and version cap as
        the license being renewed; reactivates a previously revoked
        license.

        Args:
            license_id: The license to renew.
            days: Override the plan's default validity period, in
                days.

        Returns:
            The updated issued-license record.

        Raises:
            LicenseNotFoundError: No license exists with that id.
            LicenseSigningKeyError: The vendor's signing key exists but
                is invalid (a missing one is auto-created instead —
                see :meth:`_load_private_key`).
        """
        with self._session_scope() as session:
            repo = LicenseRepository(session)
            record = repo.get_by_id(license_id)
            if record is None:
                raise LicenseNotFoundError(f"No license with id={license_id!r}.")
            customer = record.customer

            license_key, issued_at, expires_at = self._issue_key_for(
                customer_name=customer.contact_name,
                company_name=customer.company_name,
                license_type=record.license_type,
                machine_id=record.machine_id,
                licensed_version=record.licensed_version,
                days=days,
            )

            record.license_key = license_key
            record.issued_at = issued_at
            record.expires_at = expires_at
            record.status = IssuedLicenseStatus.ACTIVE
            session.flush()
            return record

    def revoke_license(self, license_id: int) -> IssuedLicense:
        """Mark a license as revoked in the vendor's own records.

        This is a bookkeeping action only: it does not remotely disable
        an already-activated Attendance Client, which requires the
        remote synchronization layer explicitly out of scope for this
        phase.

        Args:
            license_id: The license to revoke.

        Returns:
            The updated issued-license record.

        Raises:
            LicenseNotFoundError: No license exists with that id.
        """
        with self._session_scope() as session:
            repo = LicenseRepository(session)
            record = repo.get_by_id(license_id)
            if record is None:
                raise LicenseNotFoundError(f"No license with id={license_id!r}.")
            record.status = IssuedLicenseStatus.REVOKED
            session.flush()
            return record

    def get_license(self, license_id: int) -> IssuedLicense | None:
        """Fetch a single issued license by id.

        Args:
            license_id: The license to fetch.

        Returns:
            The matching license, or ``None`` if not found (or
            soft-deleted).
        """
        with self._session_scope() as session:
            return LicenseRepository(session).get_by_id(license_id)

    def search_licenses(self, query: str = "") -> list[IssuedLicense]:
        """Search issued licenses by customer company name or machine id.

        Args:
            query: A case-insensitive substring; empty returns every
                license.

        Returns:
            Matching licenses, most recently issued first.
        """
        with self._session_scope() as session:
            return LicenseRepository(session).search(query)

    def list_by_customer(self, customer_id: int) -> list[IssuedLicense]:
        """List every license issued to one customer.

        Args:
            customer_id: The customer to list licenses for.

        Returns:
            That customer's licenses, most recently issued first.
        """
        with self._session_scope() as session:
            return LicenseRepository(session).list_by_customer(customer_id)

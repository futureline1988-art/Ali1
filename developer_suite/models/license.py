"""Issued license (vendor's own record of licenses it has issued) ORM model.

A row here is the Developer Suite's own bookkeeping record of one
signed license key handed to one :class:`~developer_suite.models.customer.Customer`
— never the license *enforcement* mechanism itself, which remains
entirely :mod:`licensing` (the Ed25519 sign/verify machinery the
Attendance Client already ships with and checks at startup). This
model exists so the vendor can see, search, renew, and revoke (for
their own records) what they have issued; it does not change how the
Attendance Client validates a license key it has been given.

Reuses :class:`licensing.enums.LicenseType` directly rather than
defining a second, parallel plan enum — the platform rule against
duplicating licensing code applies to enums as much as to signing
logic.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from developer_suite.database.base import DeveloperSuiteBaseModel
from developer_suite.models.customer import Customer
from licensing.enums import LicenseType
from models.base import enum_column_type


class IssuedLicenseStatus(str, Enum):
    """The vendor's own bookkeeping status for an issued license.

    Distinct from :class:`licensing.enums.LicenseStatusCode`, which is
    the *running Attendance Client's* verification result (valid,
    expired, machine mismatch, ...) for whatever key it currently
    holds — this is instead the vendor's record of whether they still
    consider a license they issued to be in good standing.
    """

    ACTIVE = "active"
    REVOKED = "revoked"


class IssuedLicense(DeveloperSuiteBaseModel):
    """A single signed license key the vendor has issued to a customer.

    Attributes:
        customer_id: The customer this license was issued to.
        customer: The associated :class:`~developer_suite.models.customer.Customer`.
        license_type: Which plan this key grants (see
            :class:`licensing.enums.LicenseType`).
        license_key: The full, signed license key string, exactly as
            handed to the customer and exactly as
            :func:`licensing.license_key.decode_and_verify_license_key`
            would parse it.
        machine_id: If set, the machine fingerprint this key is locked
            to (see :mod:`licensing.machine_id`).
        licensed_version: The highest application version this key
            entitles the holder to run; ``None`` means unrestricted.
        issued_at: The date this key was generated.
        expires_at: The date this key stops being valid; ``None`` means
            it never expires.
        status: The vendor's own bookkeeping status (see
            :class:`IssuedLicenseStatus`). Revoking here is a
            record-keeping action only — it does not, by itself, stop
            an already-activated Attendance Client from continuing to
            accept this key, since that would require the remote
            synchronization layer explicitly out of scope for this
            phase.
    """

    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer: Mapped["Customer"] = relationship("Customer")

    license_type: Mapped[LicenseType] = mapped_column(
        enum_column_type(LicenseType), nullable=False, index=True
    )
    license_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    machine_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    licensed_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    issued_at: Mapped[date] = mapped_column(Date, nullable=False)
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[IssuedLicenseStatus] = mapped_column(
        enum_column_type(IssuedLicenseStatus),
        default=IssuedLicenseStatus.ACTIVE,
        nullable=False,
        index=True,
    )

    @property
    def is_expired(self) -> bool:
        """Whether :attr:`expires_at` has passed (always ``False`` for a never-expiring key)."""
        return self.expires_at is not None and self.expires_at < date.today()

    @property
    def days_remaining(self) -> int | None:
        """Days until :attr:`expires_at`; ``None`` for a never-expiring key.

        Negative once expired, so callers can distinguish "expires
        soon" from "already expired" without a separate check.
        """
        if self.expires_at is None:
            return None
        return (self.expires_at - date.today()).days

    @property
    def is_active(self) -> bool:
        """Whether the vendor considers this license currently in good standing."""
        return self.status is IssuedLicenseStatus.ACTIVE and not self.is_expired

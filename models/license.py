"""License ORM model — commercial licensing per company.

Enforcement of the limits declared here (rejecting a new user once
``max_users`` is reached, refusing a device registration past
``max_devices``, and so on) is a service-layer responsibility — this
module only defines the data shape and the pure, state-derived validity
checks (:attr:`License.is_expired`, :attr:`License.is_valid`).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, Integer, JSON, String
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import BaseModel, CompanyScopedMixin


class License(CompanyScopedMixin, BaseModel):
    """A commercial license grant for a company.

    A company may accumulate multiple ``License`` rows over time
    (renewals, plan upgrades); exactly one should have :attr:`is_active`
    set at any moment. Enforcing that single-active-license invariant is
    left to the service layer rather than a database constraint, since a
    portable partial unique index is not available uniformly across
    SQLite, PostgreSQL and MySQL.

    Attributes:
        license_key: Globally unique license credential string (unique
            across the whole installation, not per-company — the key
            itself is what identifies which company it belongs to).
        issued_at: Date the license was issued.
        expires_at: Date the license stops being valid; ``NULL`` means it
            never expires.
        max_users: Maximum number of :class:`~models.user.User` accounts
            allowed; ``NULL`` means unlimited.
        max_devices: Maximum number of biometric devices allowed;
            ``NULL`` means unlimited.
        max_branches: Maximum number of :class:`~models.branch.Branch`
            records allowed; ``NULL`` means unlimited.
        enabled_features: List of feature codes this license unlocks
            (e.g. ``["devices.hikvision", "reports.export_pdf"]``);
            wrapped in :class:`~sqlalchemy.ext.mutable.MutableList` so
            in-place mutation (``license.enabled_features.append(...)``)
            is tracked by the unit of work like a normal attribute
            assignment would be.
        is_active: Whether this is the company's currently-applied
            license.
        company: The owning :class:`~models.company.Company`.
    """

    license_key: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    issued_at: Mapped[date] = mapped_column(Date, nullable=False, default=date.today)
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    max_users: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_devices: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_branches: Mapped[int | None] = mapped_column(Integer, nullable=True)

    enabled_features: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON), default=list, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    company: Mapped["Company"] = relationship("Company", back_populates="licenses")  # noqa: F821

    @property
    def is_expired(self) -> bool:
        """Whether :attr:`expires_at` has passed as of today."""
        if self.expires_at is None:
            return False
        return self.expires_at < date.today()

    @property
    def is_valid(self) -> bool:
        """Whether this license is both marked active and not expired."""
        return self.is_active and not self.is_expired

    @property
    def days_remaining(self) -> int | None:
        """Days until expiration, or ``None`` if the license never expires."""
        if self.expires_at is None:
            return None
        return (self.expires_at - date.today()).days

    def has_feature(self, feature_code: str) -> bool:
        """Whether ``feature_code`` is present in :attr:`enabled_features`.

        Args:
            feature_code: A feature flag string (e.g.
                ``"devices.hikvision"``).

        Returns:
            ``True`` if the feature is enabled by this license.
        """
        return feature_code in self.enabled_features

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return (
            f"<License id={self.id!r} key={self.license_key!r} "
            f"company_id={self.company_id!r}>"
        )

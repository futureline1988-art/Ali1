"""License-related enumerations."""

from __future__ import annotations

from enum import Enum


class LicenseType(str, Enum):
    """The commercial license plans this application supports."""

    TRIAL = "trial"
    MONTHLY = "monthly"
    YEARLY = "yearly"
    LIFETIME = "lifetime"

    @property
    def label_ar(self) -> str:
        """Arabic display label."""
        return _LABELS_AR[self]

    @property
    def label_en(self) -> str:
        """English display label."""
        return _LABELS_EN[self]


_LABELS_AR: dict[LicenseType, str] = {
    LicenseType.TRIAL: "نسخة تجريبية",
    LicenseType.MONTHLY: "اشتراك شهري",
    LicenseType.YEARLY: "اشتراك سنوي",
    LicenseType.LIFETIME: "ترخيص دائم",
}

_LABELS_EN: dict[LicenseType, str] = {
    LicenseType.TRIAL: "Trial",
    LicenseType.MONTHLY: "Monthly",
    LicenseType.YEARLY: "Yearly",
    LicenseType.LIFETIME: "Lifetime",
}


class LicenseStatusCode(str, Enum):
    """The outcome of checking the currently stored (or absent) license."""

    NOT_ACTIVATED = "not_activated"
    VALID = "valid"
    EXPIRED = "expired"
    MACHINE_MISMATCH = "machine_mismatch"
    INVALID = "invalid"

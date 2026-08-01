"""Reusable input validation helpers shared by services and the UI layer.

Every function here validates the *shape* of a single value — never
uniqueness (that requires a database round trip and belongs to the
repository/service layer) and never cross-field business rules specific
to one model (those live on the model itself, e.g.
:meth:`models.leave.LeaveRequest.approve`'s state-machine guard).

Kept dependency-free (stdlib only) so both the desktop UI's live-typing
field validation and the service layer's pre-save checks can use these
without pulling in extra packages.
"""

from __future__ import annotations

import ipaddress
import re
from datetime import date
from decimal import Decimal, InvalidOperation

_EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

_IRAQ_LOCAL_MOBILE_PATTERN = re.compile(r"^07\d{9}$")
_IRAQ_INTERNATIONAL_MOBILE_PATTERN = re.compile(r"^\+964\d{9,10}$")
_GENERIC_INTERNATIONAL_PHONE_PATTERN = re.compile(r"^\+?\d{8,15}$")

_NATIONAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{5,20}$")
_EMPLOYEE_NUMBER_PATTERN = re.compile(r"^[A-Za-z0-9_-]{2,30}$")
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
_HOSTNAME_LABEL_PATTERN = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")


def is_valid_email(value: str) -> bool:
    """Whether ``value`` is a plausible email address.

    Uses a pragmatic pattern rather than a full RFC 5322 implementation
    — real-world email validation is ultimately confirmed by sending a
    message, not by a regex, so this only catches obviously malformed
    input.
    """
    return bool(_EMAIL_PATTERN.match(value.strip()))


def is_valid_phone(value: str) -> bool:
    """Whether ``value`` is a plausible phone number.

    Accepts the Iraqi local mobile format (``07XXXXXXXXX``), the Iraqi
    international format (``+964XXXXXXXXX``), and a generic
    international fallback (an optional leading ``+`` followed by 8-15
    digits) — this system targets Iraqi companies primarily but must
    also work for international ones.
    """
    candidate = value.strip()
    return bool(
        _IRAQ_LOCAL_MOBILE_PATTERN.match(candidate)
        or _IRAQ_INTERNATIONAL_MOBILE_PATTERN.match(candidate)
        or _GENERIC_INTERNATIONAL_PHONE_PATTERN.match(candidate)
    )


def normalize_phone(value: str) -> str:
    """Strip formatting characters from a phone number, keeping a leading ``+``.

    Args:
        value: A phone number, possibly containing spaces, dashes or
            parentheses (e.g. ``"+964 770 123 4567"``).

    Returns:
        The number with all non-digit characters removed, preserving a
        leading ``+`` if present (e.g. ``"+9647701234567"``).
    """
    stripped = value.strip()
    has_plus = stripped.startswith("+")
    digits_only = re.sub(r"\D", "", stripped)
    return f"+{digits_only}" if has_plus else digits_only


def is_valid_national_id(value: str) -> bool:
    """Whether ``value`` looks like a plausible national ID number.

    Deliberately lenient: national ID formats vary widely by country and
    issuing authority, so this only rejects obviously invalid input
    (empty, too short/long, or containing characters no real-world ID
    scheme uses) rather than enforcing one specific country's exact
    format.
    """
    return bool(_NATIONAL_ID_PATTERN.match(value.strip()))


def is_valid_employee_number(value: str) -> bool:
    """Whether ``value`` is a valid employee number (e.g. ``"EMP-0042"``).

    Allows letters, digits, hyphens and underscores, 2-30 characters —
    permissive enough for whatever numbering scheme a company chooses.
    """
    return bool(_EMPLOYEE_NUMBER_PATTERN.match(value.strip()))


def is_valid_username(value: str) -> bool:
    """Whether ``value`` is a valid login username.

    Allows letters, digits, dots, hyphens and underscores, 3-64
    characters, with no whitespace.
    """
    return bool(_USERNAME_PATTERN.match(value.strip()))


def is_valid_salary(value: Decimal | int | float | str) -> bool:
    """Whether ``value`` is a valid, non-negative salary amount.

    Accepts anything convertible to :class:`~decimal.Decimal`. Gives the
    UI a way to reject an invalid salary before a save attempt would
    otherwise fail against
    :attr:`~models.employee.Employee`'s database-level
    ``ck_employees_salary_non_negative`` constraint.
    """
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return False
    return decimal_value.is_finite() and decimal_value >= 0


def is_valid_date_range(start: date, end: date) -> bool:
    """Whether ``end`` is on or after ``start``.

    Shared by every model with a date range (leave requests, shift
    assignments) so the same rule is enforced consistently in the UI
    before it would otherwise fail against a database ``CHECK``
    constraint.
    """
    return end >= start


def is_valid_port(value: int) -> bool:
    """Whether ``value`` is a valid TCP/UDP port number (1-65535)."""
    return 1 <= value <= 65535


def is_valid_host(value: str) -> bool:
    """Whether ``value`` is a valid IPv4/IPv6 address or hostname.

    Used to validate a biometric device's connection address before
    attempting to reach it.
    """
    candidate = value.strip()
    if not candidate:
        return False

    try:
        ipaddress.ip_address(candidate)
        return True
    except ValueError:
        pass

    if len(candidate) > 253:
        return False
    labels = candidate.split(".")
    if all(label.isdigit() for label in labels):
        # Every label is purely numeric: no legitimate hostname looks
        # like this, so a dotted-decimal string reaching here can only
        # be a malformed IP address (e.g. an out-of-range octet like
        # "192.168.1.999") -- it already failed ip_address() above, so
        # reject it instead of accepting it as a technically-valid but
        # meaningless all-numeric hostname.
        return False
    return all(_HOSTNAME_LABEL_PATTERN.match(label) for label in labels)


def is_within_length(value: str, *, minimum: int = 0, maximum: int | None = None) -> bool:
    """Whether ``len(value)`` falls within ``[minimum, maximum]``.

    A small, generic building block for free-text fields (names,
    descriptions, notes) that do not warrant a dedicated validator
    function of their own.

    Args:
        value: The string to measure.
        minimum: Minimum allowed length, inclusive.
        maximum: Maximum allowed length, inclusive; ``None`` means no
            upper bound.
    """
    length = len(value)
    if length < minimum:
        return False
    if maximum is not None and length > maximum:
        return False
    return True

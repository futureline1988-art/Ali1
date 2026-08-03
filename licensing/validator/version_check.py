"""Compare the running application version against a license's version cap.

Not wired into :class:`~licensing.license_service.LicenseService` in
this phase — see this package's ``__init__.py``. Provided now, tested
standalone, so a later phase can adopt it as a one-line addition to
:meth:`~licensing.license_service.LicenseService.get_status` (returning
``LicenseStatusCode.VERSION_MISMATCH`` — not yet defined either;
adding it is that same later phase's job) rather than having to design
version comparison from scratch under time pressure then.
"""

from __future__ import annotations

import re

_VERSION_PATTERN = re.compile(r"^\d+(\.\d+)*$")


class InvalidVersionStringError(ValueError):
    """A version string is not a dotted sequence of non-negative integers."""


def parse_version(version_string: str) -> tuple[int, ...]:
    """Parse a dotted version string (e.g. ``"1.2.0"``) into a comparable tuple.

    Args:
        version_string: A version string of one or more dot-separated
            non-negative integers (``"1"``, ``"1.2"``, ``"1.2.0"``,
            ``"1.2.0.1"`` are all valid).

    Returns:
        The parsed components as a tuple of ints, e.g. ``(1, 2, 0)``.

    Raises:
        InvalidVersionStringError: ``version_string`` is empty, has a
            non-numeric component, or a negative component.
    """
    stripped = version_string.strip()
    if not _VERSION_PATTERN.match(stripped):
        raise InvalidVersionStringError(
            f"{version_string!r} is not a valid version string "
            "(expected dot-separated non-negative integers, e.g. '1.2.0')."
        )
    return tuple(int(part) for part in stripped.split("."))


def _compare(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    """Compare two parsed version tuples of possibly different lengths.

    Shorter tuples are treated as zero-padded on the right (``(1, 2)``
    compares equal to ``(1, 2, 0)``), matching the usual convention
    that "1.2" and "1.2.0" name the same release.

    Returns:
        Negative if ``left < right``, zero if equal, positive if
        ``left > right``.
    """
    length = max(len(left), len(right))
    padded_left = left + (0,) * (length - len(left))
    padded_right = right + (0,) * (length - len(right))
    return (padded_left > padded_right) - (padded_left < padded_right)


def is_version_licensed(running_version: str, licensed_version: str | None) -> bool:
    """Whether ``running_version`` is covered by a license's version cap.

    Args:
        running_version: The application version currently executing
            (e.g. :attr:`config.AppConfig.app_version`).
        licensed_version: The license's
            :attr:`~licensing.license_key.LicensePayload.licensed_version`.
            ``None`` means the license places no version restriction
            (always covered) — this is the case for every license
            issued before this field existed, so old keys keep working
            unchanged.

    Returns:
        ``True`` if the license covers ``running_version``.

    Raises:
        InvalidVersionStringError: Either version string is malformed.
    """
    if licensed_version is None:
        return True
    return _compare(parse_version(running_version), parse_version(licensed_version)) <= 0

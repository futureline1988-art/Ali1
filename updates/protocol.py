"""The wire-level contract the Attendance Client's update-check code must honor.

This module intentionally imports nothing from ``server`` or
``developer_suite``, only replicating the small, stable vocabulary
(:class:`UpdateType`, :class:`PackageType`) a client must reproduce to
interpret an update server's update-management API responses.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["UpdateType", "PackageType"]


class UpdateType(str, Enum):
    """Mirrors :class:`server.models.update.UpdateType`'s values exactly.

    Determines how strongly :mod:`updates.checker` pushes a discovered
    version toward the user (whether it auto-downloads, and whether
    postponing is even allowed — see
    :meth:`~updates.checker.UpdateCheckService.is_postponable`).
    """

    OPTIONAL = "optional"
    RECOMMENDED = "recommended"
    CRITICAL = "critical"
    MANDATORY = "mandatory"


class PackageType(str, Enum):
    """Mirrors :class:`server.models.update.PackageType`'s values exactly."""

    SETUP = "setup"
    PORTABLE = "portable"

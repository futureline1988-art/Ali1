"""Device connector interface: the boundary between this application's
domain and device-specific communication protocols.

Every concrete connector (ZKTeco TCP/IP, ZKTeco UDP, Hikvision) speaks
only the vocabulary defined here — :class:`RawAttendanceLog` /
:class:`RawDeviceUser` in and out — so ``services/device_service.py``
never needs to know which protocol a given device actually uses, and a
new protocol can be added later by implementing this interface once,
without touching the service layer.

No proprietary protocol is reimplemented here: every concrete connector
must be a thin, faithful adapter over its vendor's documented SDK/API,
never a reverse-engineered reimplementation of a vendor's wire format.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from models.enums import PunchType


@dataclass(frozen=True)
class RawAttendanceLog:
    """One raw attendance punch as reported by a device.

    Reported before any mapping to a known
    :class:`~models.employee.Employee` — that mapping is
    ``services/device_service.py``'s job, not the connector's.

    Attributes:
        device_user_reference: The device's own user identifier (its
            enrolled user ID/badge number), matched against
            :attr:`~models.employee.Employee.employee_number` by the
            service layer.
        punch_type: Check-in or check-out, as reported (or inferred)
            by the device.
        punch_time: When the punch occurred, converted to UTC by the
            connector.
    """

    device_user_reference: str
    punch_type: PunchType
    punch_time: datetime


@dataclass(frozen=True)
class RawDeviceUser:
    """One user as known to (or to be enrolled on) a device.

    Attributes:
        device_user_reference: The device's user identifier.
        name: Display name to enroll/report.
    """

    device_user_reference: str
    name: str


class DeviceConnectionError(Exception):
    """Raised when a connector cannot reach or communicate with a device."""


@runtime_checkable
class DeviceConnector(Protocol):
    """The operations every concrete device connector must implement.

    A connector is short-lived: obtained from
    :meth:`~devices.device_manager.DeviceManager.get_connector`, used
    for one operation (or a small batch), then :meth:`disconnect`ed —
    connectors are not required to support being reused indefinitely or
    shared across threads.
    """

    def test_connection(self) -> bool:
        """Attempt to connect and immediately disconnect.

        Returns:
            ``True`` if the device responded successfully.
        """
        ...

    def fetch_attendance_logs(self) -> list[RawAttendanceLog]:
        """Download every attendance log currently stored on the device.

        Returns:
            The device's raw attendance log entries.

        Raises:
            DeviceConnectionError: On any communication failure.
        """
        ...

    def fetch_users(self) -> list[RawDeviceUser]:
        """Download every user currently enrolled on the device.

        Returns:
            The device's enrolled users.

        Raises:
            DeviceConnectionError: On any communication failure.
        """
        ...

    def push_users(self, users: list[RawDeviceUser]) -> None:
        """Enroll or update one or more users on the device.

        Args:
            users: The users to push.

        Raises:
            DeviceConnectionError: On any communication failure.
        """
        ...

    def disconnect(self) -> None:
        """Release the underlying connection, if one is open.

        Must be safe to call even if no connection was ever
        successfully established (e.g. after a failed
        :meth:`test_connection`).
        """
        ...

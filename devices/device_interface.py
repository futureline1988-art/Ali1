"""Device connector interface: the boundary between this application's
domain and device-specific communication protocols.

Every concrete connector (ZKTeco TCP/IP, ZKTeco UDP, Hikvision, DELI
ES172) speaks only the vocabulary defined here — :class:`RawAttendanceLog` /
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


@dataclass(frozen=True)
class DeviceCapabilities:
    """What a device reports about itself: identity, capacity, and biometric support.

    Every field is best-effort and individually optional (``None``
    when a connector could not determine it) — not every protocol/
    firmware exposes all of this, and this dataclass deliberately
    never fabricates a value a connector could not actually read from
    the device.

    Attributes:
        supports_face: Whether the device's own firmware reports face
            recognition as enabled/available. This is the *only*
            face-related thing this project's ZKTeco integration can
            determine — see
            :meth:`~devices.zkteco_device.ZKTecoConnector.get_capabilities`'s
            own docstring for why template upload/remote-enrollment-
            trigger are not possible with the currently used library.
        face_template_count: How many faces are currently enrolled,
            device-wide (not per employee — the underlying protocol
            has no per-user face query).
        face_capacity: Maximum faces the device can hold.
        supports_fingerprint: Whether the device supports fingerprint
            templates at all.
        fingerprint_template_count: How many fingerprint templates are
            currently enrolled, device-wide.
        fingerprint_capacity: Maximum fingerprint templates the device
            can hold.
        user_count: How many users are currently enrolled.
        user_capacity: Maximum users the device can hold.
        attendance_log_count: How many attendance records are
            currently stored on the device.
        serial_number: Manufacturer serial number, if retrievable.
        device_model: Platform/model identifier, if retrievable.
        firmware_version: Firmware version string, if retrievable.
    """

    supports_face: bool = False
    face_template_count: int | None = None
    face_capacity: int | None = None
    supports_fingerprint: bool = False
    fingerprint_template_count: int | None = None
    fingerprint_capacity: int | None = None
    user_count: int | None = None
    user_capacity: int | None = None
    attendance_log_count: int | None = None
    serial_number: str | None = None
    device_model: str | None = None
    firmware_version: str | None = None


@dataclass(frozen=True)
class UserBiometricStatus:
    """One employee's biometric enrollment state, as read directly from a device.

    Attributes:
        fingerprint_count: How many fingerprint templates this
            specific user has enrolled (0 if none, or if the user was
            not found on the device at all).
        card_assigned: Whether an access card is assigned to this user
            on the device.
    """

    fingerprint_count: int = 0
    card_assigned: bool = False


class DeviceConnectionError(Exception):
    """Raised when a connector cannot reach or communicate with a device."""


@dataclass(frozen=True)
class ConnectionTestResult:
    """The outcome of a diagnostic connection test against a device.

    Unlike the plain boolean :meth:`DeviceConnector.test_connection`,
    this carries a precise, honest Arabic reason for a failure (network
    unreachable, invalid address, port closed, wrong communication
    key, timeout, unsupported protocol) derived from the underlying
    SDK's own exception — never guessed or fabricated — plus a raw
    technical ``detail`` string an administrator can copy into a
    support request, and, on success, the device's
    :class:`DeviceCapabilities`.

    Attributes:
        success: Whether the connection test succeeded.
        message_ar: A precise, user-facing Arabic message.
        detail: Raw technical detail (the underlying exception text),
            for a copyable diagnostic log — empty on success unless a
            capability read partially failed.
        capabilities: The device's capabilities, if the connection
            succeeded and they could be read.
    """

    success: bool
    message_ar: str
    detail: str = ""
    capabilities: DeviceCapabilities | None = None


@runtime_checkable
class DeviceConnector(Protocol):
    """The operations every concrete device connector must implement.

    A connector is short-lived: obtained from
    :meth:`~devices.device_manager.DeviceManager.get_connector`, used
    for one operation (or a small batch), then :meth:`disconnect`ed —
    connectors are not required to support being reused indefinitely or
    shared across threads.
    """

    def test_connection_detailed(self) -> ConnectionTestResult:
        """Attempt to connect, classify any failure precisely, and disconnect.

        Returns:
            A :class:`ConnectionTestResult` with a precise Arabic
            reason on failure, and the device's capabilities on
            success.
        """
        ...

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

    def get_capabilities(self) -> DeviceCapabilities:
        """Read this device's identity, capacity, and biometric support.

        Returns:
            Best-effort device info — see :class:`DeviceCapabilities`'s
            own docstring for why every field is individually optional
            and never fabricated.

        Raises:
            DeviceConnectionError: On any communication failure.
        """
        ...

    def get_user_biometric_status(self, device_user_reference: str) -> UserBiometricStatus:
        """Read one specific user's biometric enrollment state from the device.

        Args:
            device_user_reference: The device's user identifier to
                look up (matches
                :attr:`~models.employee.Employee.employee_number`).

        Returns:
            The user's status, or a zero-valued
            :class:`UserBiometricStatus` if no such user exists on the
            device.

        Raises:
            DeviceConnectionError: On any communication failure.
        """
        ...

"""ZKTeco TCP/IP and UDP connector, backed by the ``pyzk`` library.

A thin adapter only — all protocol logic lives in ``pyzk``
(https://github.com/fananimi/pyzk), which speaks ZKTeco's documented
device communication protocol. The ``zk`` package is imported lazily,
on first use, so this project never *requires* ``pyzk`` to be installed
unless a ZKTeco device is actually configured — a company using only
Hikvision devices should not need it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from devices.device_interface import DeviceConnectionError, RawAttendanceLog, RawDeviceUser
from models.enums import PunchType
from utils.logger import logger

#: pyzk's own attendance status codes. 0 = check-in, 1 = check-out,
#: 2 = break-out, 3 = break-in, 4 = overtime-in, 5 = overtime-out. Most
#: ZKTeco devices deployed in the field are configured with only the
#: two simple states, so anything not explicitly a "-out" code is
#: treated as a check-in.
_CHECK_OUT_STATUS_CODES = frozenset({1, 2, 5})


class ZKTecoConnector:
    """Connector for ZKTeco devices over TCP/IP or UDP.

    Each public method opens its own connection and leaves it open for
    :meth:`disconnect` to close — callers (``DeviceService``) always
    call ``disconnect()`` in a ``finally`` block.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        password: str | None,
        timeout: int,
        force_udp: bool,
    ) -> None:
        """Configure a connector; does not connect yet.

        Args:
            host: Device IP address or hostname.
            port: Device port (commonly ``4370``).
            password: The device's numeric communication key (pyzk
                calls this the connection "password"); ``None`` or
                empty means no key is configured.
            timeout: Socket timeout, in seconds.
            force_udp: Whether to use UDP instead of TCP/IP.
        """
        self._host = host
        self._port = port
        try:
            self._comm_password = int(password) if password else 0
        except ValueError:
            self._comm_password = 0
        self._timeout = timeout
        self._force_udp = force_udp
        self._connection = None

    def _connect(self):  # noqa: ANN202 - pyzk's connection type is only known once imported
        """Open a pyzk connection.

        Returns:
            pyzk's live connection object.

        Raises:
            DeviceConnectionError: If ``pyzk`` is not installed, or the
                device could not be reached.
        """
        try:
            from zk import ZK
            from zk.exception import ZKErrorConnection, ZKErrorResponse
        except ImportError as exc:
            raise DeviceConnectionError(
                "The 'pyzk' package is required to connect to ZKTeco devices "
                "but is not installed."
            ) from exc

        device = ZK(
            self._host,
            port=self._port,
            timeout=self._timeout,
            password=self._comm_password,
            force_udp=self._force_udp,
            ommit_ping=True,
        )
        try:
            self._connection = device.connect()
        except (ZKErrorConnection, ZKErrorResponse, OSError) as exc:
            raise DeviceConnectionError(
                f"Could not connect to ZKTeco device at {self._host}:{self._port}: {exc}"
            ) from exc
        return self._connection

    def test_connection(self) -> bool:
        """Attempt to connect and immediately disconnect."""
        try:
            self._connect()
            return True
        except DeviceConnectionError as exc:
            logger.warning("ZKTeco test_connection failed: {error}", error=str(exc))
            return False
        finally:
            self.disconnect()

    def fetch_attendance_logs(self) -> list[RawAttendanceLog]:
        """Download and map every attendance record stored on the device."""
        connection = self._connect()
        try:
            records = connection.get_attendance()
        except Exception as exc:
            raise DeviceConnectionError(f"Failed to fetch attendance logs: {exc}") from exc

        logs: list[RawAttendanceLog] = []
        for record in records:
            punch_time = record.timestamp
            punch_time = (
                punch_time.replace(tzinfo=timezone.utc)
                if punch_time.tzinfo is None
                else punch_time.astimezone(timezone.utc)
            )
            punch_type = (
                PunchType.CHECK_OUT
                if record.status in _CHECK_OUT_STATUS_CODES
                else PunchType.CHECK_IN
            )
            logs.append(
                RawAttendanceLog(
                    device_user_reference=str(record.user_id),
                    punch_type=punch_type,
                    punch_time=punch_time,
                )
            )
        return logs

    def fetch_users(self) -> list[RawDeviceUser]:
        """Download every user currently enrolled on the device."""
        connection = self._connect()
        try:
            users = connection.get_users()
        except Exception as exc:
            raise DeviceConnectionError(f"Failed to fetch users: {exc}") from exc
        return [
            RawDeviceUser(device_user_reference=str(user.user_id), name=user.name)
            for user in users
        ]

    def push_users(self, users: list[RawDeviceUser]) -> None:
        """Enroll or update users on the device.

        Note:
            ``uid=0`` requests pyzk/the device auto-assign an internal
            storage slot; this project does not track ZKTeco's internal
            numeric ``uid`` separately from ``device_user_reference``,
            so re-pushing the same user updates their name rather than
            creating a duplicate slot only if the device itself
            deduplicates by ``user_id`` — this is standard ZKTeco
            firmware behavior but has not been verified against every
            firmware revision.
        """
        connection = self._connect()
        try:
            for user in users:
                connection.set_user(uid=0, name=user.name, user_id=user.device_user_reference)
        except Exception as exc:
            raise DeviceConnectionError(f"Failed to push users: {exc}") from exc

    def disconnect(self) -> None:
        """Close the underlying pyzk connection, if one is open."""
        if self._connection is not None:
            try:
                self._connection.disconnect()
            except Exception:  # noqa: BLE001 - best-effort cleanup, never raise on disconnect
                pass
            self._connection = None

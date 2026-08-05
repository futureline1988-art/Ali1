"""ZKTeco TCP/IP and UDP connector, backed by the ``pyzk`` library.

A thin adapter only — all protocol logic lives in ``pyzk``
(https://github.com/fananimi/pyzk), which speaks ZKTeco's documented
device communication protocol. The ``zk`` package is imported lazily,
on first use, so this project never *requires* ``pyzk`` to be installed
unless a ZKTeco device is actually configured — a company using only
Hikvision devices should not need it.

Face enrollment capability, verified directly against ``pyzk==0.9``
(the latest release on PyPI as of this writing — no newer version
exists): the library has no module or method to read/write a face
*template* at all (compare ``zk/finger.py``, which fully implements
fingerprint templates), and no protocol command to remotely *trigger*
on-device face enrollment either — ``enroll_user()`` sends
``CMD_STARTENROLL``, but its response parsing is fingerprint-specific
(it decodes a "finger duplicate" status code and a 0-9 ``temp_id``
fingerprint slot), and ``zk/const.py`` defines no face-specific enroll
command. The only face-related information this library can honestly
provide is device-wide *capability metadata* —
:meth:`ZKTecoConnector.get_capabilities` reports exactly that
(``get_face_fun_on()``, ``get_face_version()``, and the enrolled-face
count/capacity from ``read_sizes()``) and nothing more; there is no
per-employee face-enrolled query or face-template delete either.
Fingerprint templates, by contrast, are fully supported by ``pyzk``
(read, write, delete, per-user) — see
:meth:`ZKTecoConnector.get_user_biometric_status`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from devices.device_interface import (
    ConnectionTestResult,
    DeviceCapabilities,
    DeviceConnectionError,
    RawAttendanceLog,
    RawDeviceUser,
    UserBiometricStatus,
)
from models.enums import PunchType
from utils.logger import logger

#: pyzk's own attendance status codes. 0 = check-in, 1 = check-out,
#: 2 = break-out, 3 = break-in, 4 = overtime-in, 5 = overtime-out. Most
#: ZKTeco devices deployed in the field are configured with only the
#: two simple states, so anything not explicitly a "-out" code is
#: treated as a check-in.
_CHECK_OUT_STATUS_CODES = frozenset({1, 2, 5})


def _classify_zkteco_error(cause: BaseException | None, host: str, port: int) -> tuple[str, str]:
    """Map a pyzk connection failure to a precise Arabic message.

    Args:
        cause: The original exception :meth:`ZKTecoConnector._connect`
            caught (``ZKErrorConnection``/``ZKErrorResponse``/
            ``ZKNetworkError``/``OSError``/``ImportError``, or
            ``None`` if unavailable) — inspected instead of the
            already-generic :class:`~devices.device_interface.DeviceConnectionError`
            wrapper, since only the original carries the real reason.
        host: The device address that was attempted, for the message.
        port: The device port that was attempted, for the message.

    Returns:
        ``(message_ar, detail)`` — a precise Arabic message and the
        raw technical detail for a copyable diagnostic log.
    """
    detail = str(cause) if cause is not None else ""

    if isinstance(cause, ImportError):
        return "حزمة الاتصال بأجهزة ZKTeco (pyzk) غير مثبتة على هذا الجهاز.", detail

    try:
        from zk.exception import ZKErrorResponse
    except ImportError:
        ZKErrorResponse = ()  # noqa: N806 - sentinel when pyzk itself is missing

    if ZKErrorResponse and isinstance(cause, ZKErrorResponse):
        if "unauth" in detail.lower():
            return "مفتاح الاتصال غير صحيح.", detail
        return "الجهاز لا يدعم البروتوكول المحدد أو رفض الاتصال.", detail

    lowered = detail.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "انتهت مهلة الاتصال.", detail
    if "refused" in lowered:
        return f"المنفذ {port} غير متاح على الجهاز.", detail
    if "unreachable" in lowered or "no route to host" in lowered or "host is down" in lowered:
        return "الجهاز غير متصل بالشبكة.", detail
    if detail:
        return f"تعذر الاتصال بالجهاز على {host}:{port}: {detail}", detail
    return f"تعذر الاتصال بالجهاز على {host}:{port}.", detail


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
            from zk.exception import ZKErrorConnection, ZKErrorResponse, ZKNetworkError
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
        except (ZKErrorConnection, ZKErrorResponse, ZKNetworkError, OSError) as exc:
            # pyzk's own ``__send_command`` wraps every transport-level
            # failure (connection refused, timed out, unreachable, ...)
            # into ``ZKNetworkError`` — a sibling of ``ZKErrorConnection``,
            # not a subclass, so it must be listed explicitly here or it
            # would propagate uncaught (verified against pyzk 0.9's own
            # source, not assumed).
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

    def test_connection_detailed(self) -> ConnectionTestResult:
        """Attempt to connect, classify any failure precisely, and disconnect.

        Distinguishes the failure reasons pyzk's own exceptions
        actually expose (see this module's docstring and
        :func:`_classify_zkteco_error`) rather than collapsing every
        failure into one generic message.
        """
        try:
            self._connect()
        except DeviceConnectionError as exc:
            message_ar, detail = _classify_zkteco_error(exc.__cause__, self._host, self._port)
            self.disconnect()
            return ConnectionTestResult(success=False, message_ar=message_ar, detail=detail)

        try:
            capabilities = self.get_capabilities()
        except DeviceConnectionError as exc:
            self.disconnect()
            return ConnectionTestResult(
                success=True,
                message_ar="تم الاتصال بنجاح، لكن تعذرت قراءة معلومات الجهاز.",
                detail=str(exc),
            )

        self.disconnect()
        return ConnectionTestResult(success=True, message_ar="تم الاتصال بنجاح.", capabilities=capabilities)

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
        """Enroll or update users on the device, preserving each user's existing device UID.

        pyzk's ``ZK.set_user(uid=...)`` treats ``uid`` as a literal
        internal storage slot number, not a "give me a fresh slot"
        sentinel — passing a fixed value (this connector previously,
        incorrectly, always passed ``uid=0``) makes every pushed user
        overwrite the *same* slot instead of getting their own. Passing
        ``uid=None`` is what actually asks pyzk to auto-assign a fresh,
        collision-free slot (it tracks the next free slot internally
        and increments it after every create — see ``zk.base.ZK.set_user``/
        ``next_uid``).

        To keep a given employee's device UID *stable* across repeated
        pushes (re-syncs, device reconnects) rather than silently
        handing them a new slot every time, this method always looks
        up the device's *current* enrolled users first and reuses the
        matching existing UID (matched by ``user_id`` ==
        :attr:`~devices.device_interface.RawDeviceUser.device_user_reference`)
        when one already exists; only a genuinely new
        ``device_user_reference`` gets ``uid=None`` (auto-assign).
        Deliberately never caches this mapping outside of one call —
        always re-reading it fresh from the device is what keeps it
        correct after someone edits/re-images the device directly,
        rather than trusting a local record that could drift.

        Args:
            users: The users to push. Employees already enrolled with
                this ``device_user_reference`` keep their existing UID;
                new ones get a UID the device itself assigns, so two
                users can never collide.
        """
        connection = self._connect()
        try:
            existing_uid_by_reference = {
                str(existing_user.user_id): existing_user.uid
                for existing_user in connection.get_users()
            }
            for user in users:
                connection.set_user(
                    uid=existing_uid_by_reference.get(user.device_user_reference),
                    name=user.name,
                    user_id=user.device_user_reference,
                )
        except Exception as exc:
            raise DeviceConnectionError(f"Failed to push users: {exc}") from exc

    def get_capabilities(self) -> DeviceCapabilities:
        """Read this device's identity, capacity, and biometric support.

        Face support is deliberately reported as capability metadata
        only — ``pyzk`` (this connector's only ZKTeco library) has no
        API to read or write a face *template*, and no protocol
        command to remotely trigger on-device face enrollment either
        (verified directly against the installed library: no
        ``face``-template module exists, and ``enroll_user()``'s
        ``CMD_STARTENROLL`` response parsing is fingerprint-specific).
        ``get_face_fun_on()``/``get_face_version()``/``read_sizes()``'s
        face counters are the only face-related information this
        connector can honestly obtain — see
        :mod:`devices.zkteco_device`'s module docstring.

        Returns:
            Best-effort capability/identity info.

        Raises:
            DeviceConnectionError: On any communication failure.
        """
        connection = self._connect()
        try:
            connection.read_sizes()
            face_fun_on = connection.get_face_fun_on()
            face_version = connection.get_face_version()
            supports_face = bool(face_fun_on) and bool(face_version)

            serial_number = self._try(connection.get_serialnumber)
            device_model = self._try(connection.get_platform) or self._try(
                connection.get_device_name
            )
            firmware_version = self._try(connection.get_firmware_version)

            return DeviceCapabilities(
                supports_face=supports_face,
                face_template_count=getattr(connection, "faces", None),
                face_capacity=getattr(connection, "faces_cap", None),
                supports_fingerprint=True,
                fingerprint_template_count=getattr(connection, "fingers", None),
                fingerprint_capacity=getattr(connection, "fingers_cap", None),
                user_count=getattr(connection, "users", None),
                user_capacity=getattr(connection, "users_cap", None),
                attendance_log_count=getattr(connection, "records", None),
                serial_number=serial_number,
                device_model=device_model,
                firmware_version=firmware_version,
            )
        except Exception as exc:
            raise DeviceConnectionError(f"Failed to read device capabilities: {exc}") from exc

    def get_user_biometric_status(self, device_user_reference: str) -> UserBiometricStatus:
        """Read one specific user's fingerprint-template count and card assignment.

        Face enrollment cannot be checked per-user (see
        :meth:`get_capabilities`'s own docstring) — this method never
        reports on face at all, only fingerprint and card, which pyzk
        genuinely supports per user.

        Args:
            device_user_reference: The device's user identifier to
                look up.

        Raises:
            DeviceConnectionError: On any communication failure.
        """
        connection = self._connect()
        try:
            matched_user = next(
                (
                    user
                    for user in connection.get_users()
                    if str(user.user_id) == device_user_reference
                ),
                None,
            )
            if matched_user is None:
                return UserBiometricStatus(fingerprint_count=0, card_assigned=False)

            templates = connection.get_templates()
            fingerprint_count = sum(
                1
                for template in templates
                if template.uid == matched_user.uid and template.valid
            )
            return UserBiometricStatus(
                fingerprint_count=fingerprint_count,
                card_assigned=bool(matched_user.card),
            )
        except Exception as exc:
            raise DeviceConnectionError(
                f"Failed to read biometric status for {device_user_reference!r}: {exc}"
            ) from exc

    @staticmethod
    def _try(reader):  # noqa: ANN001, ANN205 - small internal helper, callable in/optional str out
        """Call a pyzk info-reader, swallowing failures into ``None``.

        Several ``~`` option reads (serial number, platform name,
        firmware version) are not universally implemented across every
        ZKTeco firmware revision; a device that does not support one
        should not make the whole capability read fail.
        """
        try:
            return reader()
        except Exception:  # noqa: BLE001 - best-effort info only
            return None

    def disconnect(self) -> None:
        """Close the underlying pyzk connection, if one is open."""
        if self._connection is not None:
            try:
                self._connection.disconnect()
            except Exception:  # noqa: BLE001 - best-effort cleanup, never raise on disconnect
                pass
            self._connection = None

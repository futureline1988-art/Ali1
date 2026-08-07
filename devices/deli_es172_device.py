"""Connector for the DELI ES172, per the official DELI Offline SDK Protocol.

The device exposes a JSON-over-HTTP control endpoint, confirmed both
against the real device and against the vendor's own "DELI offline SDK
protocol" document (2025-01-25):

    POST http(s)://<device-ip>/control?api_key=<API_KEY>
    Content-Type: application/json

    {"mid": "a123456", "cmd": "<Command>", "payload": {...}}
    -> {"mid": "a123456", "result": "<ResultOrError>", "payload": {...}}

Authentication (from the document's "Security Configuration" section):
the API key is only enforced once ``SetSecurityConfig`` has explicitly
set one -- "the device will only accept commands if the key matches
the currently set value". A factory-fresh device (like the physical
test unit this project was built against, whose on-device "API Key for
SDK" field is blank) has never had a key set, so read-only commands are
expected to work with an empty/omitted ``api_key`` query parameter.

``SetSecurityConfig`` is deliberately NOT implemented here yet: its
documented payload bundles ``api_key``, ``enable_http``, and
``tls_conf`` together in a single call, and unlike ``SetUserInfo`` (which
explicitly documents that fields "can be omitted to keep the current
value"), the document never states that any ``SetSecurityConfig`` field
can be safely omitted. Sending a payload that inadvertently resets
``enable_http``/``tls_conf`` on a device only reachable over plain HTTP
could lock this application out of the device entirely. Until that is
proven safe from the official document (or from a vendor-confirmed
example), this connector never calls ``SetSecurityConfig``.

This module has two layers:

- :func:`test_get_version_info` -- the original, standalone
  GetVersionInfo-only helper used by ``ui.devices.DeliConnectionTestDialog``
  to prove the endpoint/authentication/framing before anything else was
  built on top of it. Kept as-is (exact signature and return shape) so
  that dialog and its tests keep working unchanged.
- :class:`DeliES172Connector` -- the real
  :class:`~devices.device_interface.DeviceConnector` implementation,
  wired into :mod:`devices.device_manager` under
  :attr:`~models.enums.DeviceProtocol.DELI_ES172`, covering every
  read-only command the official document defines (GetVersionInfo,
  GetDeviceUid, GetDeviceCapabilities, GetCapacityLimit,
  GetCurrentUsage, GetUserIdList, GetUserInfo, GetAttendLog) plus the
  documented write commands the :class:`~devices.device_interface.DeviceConnector`
  protocol requires (SetUserInfo for ``push_users``). Destructive
  commands (``EraseAttendLog``, and ``DeviceControl``'s
  ``ClearUsers``/``ClearAllData`` actions) are never sent by this
  connector.

Pagination follows the document's own "Communication example" tables
exactly: the first request of a paged command omits ``start_pos``; the
device's ``next_page_pos`` (user list) / ``next_pos`` (attendance log)
becomes the next request's ``start_pos``; the absence of that field in
a response means the list is exhausted.
"""

from __future__ import annotations

import json as json_module
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from devices.device_interface import (
    ConnectionTestResult,
    DeviceCapabilities,
    DeviceConnectionError,
    RawAttendanceLog,
    RawDeviceUser,
    UserBiometricStatus,
)
from models.enums import PunchType

DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_PORT = 80

#: The one device-reported error code the document documents by
#: example ("Error Response" section) that this connector treats as a
#: normal negative result rather than a communication failure -- a
#: user genuinely not existing on the device, not a broken connection.
_ERROR_CODE_USER_NOT_FOUND = "user_id_not_exists"

#: One outcome per distinguishable failure/success mode this client can
#: actually tell apart -- deliberately not a Python Enum so callers can
#: compare plain strings without an extra import; values never shown to
#: the user directly (see DeliVersionInfoResult.message_ar for that).
OUTCOME_NETWORK_ERROR = "network_error"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_AUTH_ERROR = "auth_error"
OUTCOME_INVALID_JSON = "invalid_json"
OUTCOME_HTTP_ERROR = "http_error"
OUTCOME_SUCCESS = "success"


@dataclass
class DeliVersionInfoResult:
    """The outcome of one GetVersionInfo attempt against a DELI ES172.

    Attributes:
        outcome: One of the ``OUTCOME_*`` constants above.
        message_ar: A precise, user-facing Arabic message.
        http_status: The HTTP status code, if a response was received
            at all.
        raw_response_text: The device's raw response body, if any --
            shown to the operator verbatim so they can judge success
            even before the response schema is fully confirmed.
        parsed_response: The response parsed as JSON, if it was valid
            JSON.
        error_detail: Raw technical detail (the underlying exception
            text) for a copyable diagnostic log -- the API key is
            always redacted from this before it is ever stored or
            displayed.
    """

    outcome: str
    message_ar: str
    http_status: int | None = None
    raw_response_text: str | None = None
    parsed_response: Any | None = None
    error_detail: str | None = None


def _redact_api_key(text: str, api_key: str) -> str:
    """Replace every occurrence of the raw API key in ``text`` with a placeholder.

    Needed because the key is sent as a URL query parameter --
    ``requests``' own exception messages (e.g. on a connection failure)
    echo the full request URL, including the key, and this client must
    never let that reach a log file or the screen in plaintext.
    """
    if not api_key:
        return text
    return text.replace(api_key, "***")


def _build_url(host: str, api_key: str) -> str:
    return f"http://{host}/control?api_key={api_key}"


def test_get_version_info(
    host: str, api_key: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> DeliVersionInfoResult:
    """Send the device-confirmed, read-only GetVersionInfo command.

    Args:
        host: The device's IP address or hostname, entered by the
            operator -- never hard-coded, never assumed.
        api_key: The device's "API Key for SDK", entered by the
            operator -- never logged or persisted by this function.
        timeout: Request timeout in seconds.

    Returns:
        A :class:`DeliVersionInfoResult` describing exactly what
        happened. Never raises -- every failure mode this client can
        distinguish is reported through the return value instead.
    """
    url = _build_url(host, api_key)
    body = {"mid": "1", "cmd": "GetVersionInfo", "payload": {}}

    try:
        response = requests.post(
            url, json=body, headers={"Content-Type": "application/json"}, timeout=timeout
        )
    except requests.exceptions.Timeout:
        return DeliVersionInfoResult(
            outcome=OUTCOME_TIMEOUT,
            message_ar="انتهت مهلة الاتصال بالجهاز. تحقق من عنوان IP ومن أن الجهاز متصل بالشبكة.",
        )
    except requests.exceptions.ConnectionError as exc:
        return DeliVersionInfoResult(
            outcome=OUTCOME_NETWORK_ERROR,
            message_ar="تعذر الوصول إلى الجهاز على الشبكة. تحقق من عنوان IP ومن اتصال الشبكة.",
            error_detail=_redact_api_key(str(exc), api_key),
        )
    except requests.exceptions.RequestException as exc:
        return DeliVersionInfoResult(
            outcome=OUTCOME_NETWORK_ERROR,
            message_ar="حدث خطأ أثناء الاتصال بالجهاز.",
            error_detail=_redact_api_key(str(exc), api_key),
        )

    raw_text = response.text

    if response.status_code in (401, 403):
        return DeliVersionInfoResult(
            outcome=OUTCOME_AUTH_ERROR,
            message_ar=f"مفتاح API غير صحيح أو مرفوض من الجهاز (رمز الاستجابة {response.status_code}).",
            http_status=response.status_code,
            raw_response_text=raw_text,
        )

    try:
        parsed = response.json()
    except (json_module.JSONDecodeError, ValueError):
        parsed = None
        if not (200 <= response.status_code < 300):
            return DeliVersionInfoResult(
                outcome=OUTCOME_HTTP_ERROR,
                message_ar=f"رد الجهاز برمز حالة HTTP {response.status_code} ولم يكن الرد بصيغة JSON.",
                http_status=response.status_code,
                raw_response_text=raw_text,
            )
        return DeliVersionInfoResult(
            outcome=OUTCOME_INVALID_JSON,
            message_ar="استجاب الجهاز، لكن الرد لم يكن بصيغة JSON صحيحة.",
            http_status=response.status_code,
            raw_response_text=raw_text,
        )

    if not (200 <= response.status_code < 300):
        return DeliVersionInfoResult(
            outcome=OUTCOME_HTTP_ERROR,
            message_ar=f"رد الجهاز برمز حالة HTTP {response.status_code}.",
            http_status=response.status_code,
            raw_response_text=raw_text,
            parsed_response=parsed,
        )

    return DeliVersionInfoResult(
        outcome=OUTCOME_SUCCESS,
        message_ar="تم الاتصال بالجهاز والحصول على استجابة JSON صحيحة لأمر GetVersionInfo. "
        "راجع الاستجابة المفسَّرة أدناه للتأكد من أنها تحتوي على معلومات إصدار الجهاز فعلياً.",
        http_status=response.status_code,
        raw_response_text=raw_text,
        parsed_response=parsed,
    )


class DeliCommandError(DeviceConnectionError):
    """A DELI ES172 command failed with a device-reported error code.

    Distinguishes a normal negative response (the device understood the
    request and explicitly rejected it -- see the document's "Error
    Response" section, e.g. ``code: "user_id_not_exists"``) from a
    communication failure, so callers that expect a specific code
    (like "this user does not exist") can handle it without treating
    every device-level error the same way.

    Attributes:
        code: The device's own ``payload.code`` error identifier.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _classify_deli_connection_error(
    exc: requests.exceptions.RequestException, api_key: str
) -> tuple[str, str]:
    """Map a low-level request failure to a precise Arabic message + detail.

    Mirrors the classification style already used for ZKTeco/Hikvision
    (see ``devices/zkteco_device.py``/``devices/hikvision_device.py``)
    so DELI failures read consistently with every other protocol's
    diagnostic messages.
    """
    detail = _redact_api_key(str(exc), api_key)
    if isinstance(exc, requests.exceptions.Timeout):
        return "انتهت مهلة الاتصال.", detail
    if isinstance(exc, requests.exceptions.ConnectionError):
        if "refused" in detail.lower():
            return "المنفذ غير متاح على الجهاز.", detail
        return "الجهاز غير متصل بالشبكة.", detail
    return "تعذر الاتصال بالجهاز.", detail


class DeliES172Connector:
    """Connector for the DELI ES172 over its official JSON-over-HTTP control API.

    See this module's own docstring for the protocol source and for
    why ``SetSecurityConfig`` is not implemented yet.
    """

    def __init__(self, *, host: str, port: int, api_key: str | None, timeout: float) -> None:
        """Configure a connector; does not connect yet.

        Args:
            host: Device IP address or hostname -- always the value
                stored on the :class:`~models.device.Device` record,
                never hard-coded.
            port: Device HTTP port (the official document's examples
                use plain ``http://device-ip/control?...`` with no
                port, i.e. the standard port 80; some deployments may
                still front it on a different port, hence configurable).
            api_key: The device's "API Key for SDK". Empty/``None`` is
                valid and expected for a factory-fresh, never-configured
                device (see module docstring) -- sent as an empty query
                parameter, never fabricated.
            timeout: HTTP request timeout, in seconds.
        """
        self._host = host
        self._port = port
        self._api_key = api_key or ""
        self._timeout = timeout

    @property
    def _host_with_port(self) -> str:
        return f"{self._host}:{self._port}"

    def _call(self, cmd: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one command and return its response ``payload`` dict.

        Args:
            cmd: The DELI SDK command name (e.g. ``"GetVersionInfo"``).
            payload: The command's request payload, per the official
                document; omitted/``None`` becomes ``{}``.

        Returns:
            The response's ``payload`` object (empty dict if the
            device sent none).

        Raises:
            DeliCommandError: The device responded with
                ``"result": "Error"`` -- carries the device's own
                ``code``.
            DeviceConnectionError: Any other communication failure
                (network error, timeout, non-2xx HTTP status, or a
                response that was not valid JSON).
        """
        url = _build_url(self._host_with_port, self._api_key)
        body = {"mid": "1", "cmd": cmd, "payload": payload or {}}

        try:
            response = requests.post(
                url,
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            )
        except requests.exceptions.RequestException as exc:
            message_ar, detail = _classify_deli_connection_error(exc, self._api_key)
            raise DeviceConnectionError(f"{message_ar} ({cmd}) {detail}") from exc

        if response.status_code in (401, 403):
            raise DeviceConnectionError(
                f"DELI ES172 rejected the API key for {cmd} (HTTP {response.status_code})."
            )

        try:
            parsed = response.json()
        except (json_module.JSONDecodeError, ValueError) as exc:
            raise DeviceConnectionError(
                f"DELI ES172 {cmd} returned a non-JSON response (HTTP {response.status_code})."
            ) from exc

        if not (200 <= response.status_code < 300):
            raise DeviceConnectionError(
                f"DELI ES172 {cmd} returned HTTP {response.status_code}: {response.text}"
            )

        if not isinstance(parsed, dict):
            raise DeviceConnectionError(f"DELI ES172 {cmd} returned an unexpected response shape.")

        if parsed.get("result") == "Error":
            error_payload = parsed.get("payload") or {}
            code = str(error_payload.get("code") or "unknown_error")
            raise DeliCommandError(code, f"DELI ES172 {cmd} failed: {code}")

        payload_out = parsed.get("payload")
        return payload_out if isinstance(payload_out, dict) else {}

    def test_connection(self) -> bool:
        """Whether GetVersionInfo succeeds against the configured device."""
        try:
            self._call("GetVersionInfo")
            return True
        except DeviceConnectionError:
            return False

    def test_connection_detailed(self) -> ConnectionTestResult:
        """Send GetVersionInfo, classify any failure, and read capabilities on success."""
        try:
            self._call("GetVersionInfo")
        except DeliCommandError as exc:
            return ConnectionTestResult(
                success=False,
                message_ar="رفض الجهاز الأمر (قد يكون مفتاح API غير صحيح).",
                detail=str(exc),
            )
        except DeviceConnectionError as exc:
            return ConnectionTestResult(success=False, message_ar=str(exc))

        try:
            capabilities = self.get_capabilities()
        except DeviceConnectionError as exc:
            return ConnectionTestResult(
                success=True,
                message_ar="تم الاتصال بنجاح، لكن تعذرت قراءة معلومات الجهاز.",
                detail=str(exc),
            )
        return ConnectionTestResult(
            success=True, message_ar="تم الاتصال بنجاح.", capabilities=capabilities
        )

    def get_capabilities(self) -> DeviceCapabilities:
        """Read device identity/capacity/biometric support via the documented read-only commands.

        Combines GetVersionInfo, GetDeviceCapabilities, GetCapacityLimit,
        and GetCurrentUsage -- every field is exactly what the device
        reported, never fabricated; ``device_model`` is left ``None``
        since the document has no field for it (only a combined
        ``firmware_version`` string).

        Raises:
            DeviceConnectionError: On any communication failure.
        """
        version_info = self._call("GetVersionInfo")
        device_caps = self._call("GetDeviceCapabilities")
        try:
            capacity_limit = self._call("GetCapacityLimit")
        except DeviceConnectionError:
            capacity_limit = {}
        try:
            current_usage = self._call("GetCurrentUsage")
        except DeviceConnectionError:
            current_usage = {}

        return DeviceCapabilities(
            supports_face=bool(device_caps.get("face", False)),
            face_template_count=current_usage.get("face_count"),
            face_capacity=capacity_limit.get("max_face"),
            supports_fingerprint=bool(device_caps.get("fingerprint", False)),
            fingerprint_template_count=current_usage.get("fp_count"),
            fingerprint_capacity=capacity_limit.get("max_fp"),
            user_count=current_usage.get("user_count"),
            user_capacity=capacity_limit.get("max_user"),
            attendance_log_count=current_usage.get("attend_log_count"),
            firmware_version=version_info.get("firmware_version"),
        )

    def fetch_users(self) -> list[RawDeviceUser]:
        """Download every user via paginated GetUserIdList + GetUserInfo.

        Pagination follows the document's own example exactly: the
        first request omits ``start_pos``; each subsequent request uses
        the previous response's ``next_page_pos``; the list is
        exhausted once a response has no ``next_page_pos``.

        Raises:
            DeviceConnectionError: On any communication failure.
        """
        users: list[RawDeviceUser] = []
        start_pos: int | None = None
        while True:
            list_payload = self._call(
                "GetUserIdList", {"start_pos": start_pos} if start_pos is not None else {}
            )
            for user_id in list_payload.get("user_id", []):
                try:
                    info = self._call("GetUserInfo", {"id": user_id})
                except DeliCommandError as exc:
                    if exc.code == _ERROR_CODE_USER_NOT_FOUND:
                        continue
                    raise
                users.append(
                    RawDeviceUser(
                        device_user_reference=str(info.get("id", user_id)),
                        name=info.get("name") or "",
                    )
                )
            next_page_pos = list_payload.get("next_page_pos")
            if next_page_pos is None:
                break
            start_pos = next_page_pos
        return users

    def push_users(self, users: list[RawDeviceUser]) -> None:
        """Enroll or update users via the documented SetUserInfo command.

        Only ``id``/``name`` are sent -- every other field (department,
        privilege, password, card, biometric templates) is omitted,
        which the document states keeps the current value unchanged
        for an existing user (and is simply unset for a newly created
        one).

        Raises:
            DeviceConnectionError: On any communication failure.
        """
        for user in users:
            self._call(
                "SetUserInfo",
                {"id": user.device_user_reference, "name": user.name},
            )

    def fetch_attendance_logs(self) -> list[RawAttendanceLog]:
        """Download every attendance log via paginated GetAttendLog.

        Pagination follows the document's own example: the first
        request omits ``start_pos``; each subsequent request uses the
        previous response's ``next_pos``; the log is exhausted once a
        response has no ``next_pos``.

        Note:
            The official document's ``logs`` entries carry ``mode``
            (the verification method used: face/fp/card/pwd) but no
            check-in/check-out direction field at all. This is the same
            limitation this project's Hikvision connector already has
            (see ``devices/hikvision_device.py:fetch_attendance_logs``'s
            own docstring) -- every entry is reported as
            :attr:`~models.enums.PunchType.CHECK_IN`, and the service
            layer's daily first-in/last-out aggregation is what actually
            derives check-in/check-out from the punch sequence.

        Raises:
            DeviceConnectionError: On any communication failure.
        """
        logs: list[RawAttendanceLog] = []
        start_pos: int | None = None
        while True:
            log_payload = self._call(
                "GetAttendLog", {"start_pos": start_pos} if start_pos is not None else {}
            )
            for entry in log_payload.get("logs", []):
                user_id = entry.get("user_id")
                time_str = entry.get("time")
                if not user_id or not time_str:
                    continue
                punch_time = datetime.fromisoformat(time_str)
                punch_time = (
                    punch_time.replace(tzinfo=timezone.utc)
                    if punch_time.tzinfo is None
                    else punch_time.astimezone(timezone.utc)
                )
                logs.append(
                    RawAttendanceLog(
                        device_user_reference=str(user_id),
                        punch_type=PunchType.CHECK_IN,
                        punch_time=punch_time,
                    )
                )
            next_pos = log_payload.get("next_pos")
            if next_pos is None:
                break
            start_pos = next_pos
        return logs

    def get_user_biometric_status(self, device_user_reference: str) -> UserBiometricStatus:
        """Read one user's fingerprint/card status via GetUserInfo.

        Returns:
            The user's status, or a zero-valued
            :class:`UserBiometricStatus` if the device reports
            ``user_id_not_exists`` for this reference.

        Raises:
            DeviceConnectionError: On any other communication failure.
        """
        try:
            info = self._call("GetUserInfo", {"id": device_user_reference})
        except DeliCommandError as exc:
            if exc.code == _ERROR_CODE_USER_NOT_FOUND:
                return UserBiometricStatus(fingerprint_count=0, card_assigned=False)
            raise

        fp = info.get("fp")
        fingerprint_count = len(fp) if isinstance(fp, list) else 0
        return UserBiometricStatus(
            fingerprint_count=fingerprint_count, card_assigned=bool(info.get("card"))
        )

    def disconnect(self) -> None:
        """No-op -- this connector is stateless HTTP, no connection to release."""

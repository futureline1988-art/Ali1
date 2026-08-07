"""Client for the DELI ES172's discovered HTTP control API.

Confirmed against the real device (see the DELI ES172 investigation in
the project history: the port-5005 protocol remained unidentified even
after extended passive listening and generic probing, but the device
also exposes a JSON-over-HTTP control endpoint):

    POST http://<device-ip>/control?api_key=<API_KEY>
    Content-Type: application/json

    {"mid": "1", "cmd": "GetVersionInfo", "payload": {}}

This module implements exactly that one, read-only command, to prove
the endpoint/authentication/framing actually work against the physical
device before anything else is built on top of it. It intentionally
does NOT implement employee upload/download, attendance log download,
or any write/enrollment command yet -- those need their own commands
confirmed against the real device first, the same way GetVersionInfo
now has been.

This is deliberately NOT wired into :mod:`devices.device_manager` or
:class:`~models.enums.DeviceProtocol` -- it is a standalone diagnostic
client for proving this one command, not a general-purpose
:class:`~devices.device_interface.DeviceConnector` implementation. That
comes once more commands (device info persistence, employee list,
attendance log) are each confirmed the same evidence-driven way.

The response schema is not assumed either: the caller always gets both
the raw response text and, if it parses, the parsed JSON, so a human
can judge success from the device's actual returned content rather
than a guessed schema.
"""

from __future__ import annotations

import json as json_module
from dataclasses import dataclass
from typing import Any

import requests

DEFAULT_TIMEOUT_SECONDS = 8.0

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

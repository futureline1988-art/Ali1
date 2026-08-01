"""Hikvision connector, using the device's ISAPI HTTP interface.

A thin adapter over Hikvision's documented ISAPI (the same interface
their own SDK and web UI use) via HTTP digest authentication — no
proprietary protocol reimplementation. Exact JSON field names can vary
slightly across firmware revisions; this targets the commonly
documented ISAPI shape for access-control/attendance terminals.
"""

from __future__ import annotations

from datetime import datetime, timezone

import requests
from requests.auth import HTTPDigestAuth

from devices.device_interface import DeviceConnectionError, RawAttendanceLog, RawDeviceUser
from models.enums import PunchType

#: ISAPI AcsEvent "major" event category for access-control events
#: (which includes attendance punches on combined access-control /
#: time-attendance terminals).
_ACS_EVENT_MAJOR_ACCESS_CONTROL = 5


class HikvisionConnector:
    """Connector for Hikvision access-control/attendance terminals."""

    def __init__(
        self, *, host: str, port: int, username: str, password: str, timeout: int
    ) -> None:
        """Configure a connector; does not connect yet.

        Args:
            host: Device IP address or hostname.
            port: Device HTTP port (commonly ``80``).
            username: ISAPI account username.
            password: ISAPI account password.
            timeout: HTTP request timeout, in seconds.
        """
        self._base_url = f"http://{host}:{port}"
        self._auth = HTTPDigestAuth(username, password)
        self._timeout = timeout
        self._session: requests.Session | None = None

    def _get_session(self) -> requests.Session:
        """Return the lazily-created, digest-authenticated HTTP session."""
        if self._session is None:
            self._session = requests.Session()
            self._session.auth = self._auth
        return self._session

    def test_connection(self) -> bool:
        """Whether the device's ISAPI responds successfully."""
        try:
            response = self._get_session().get(
                f"{self._base_url}/ISAPI/System/deviceInfo", timeout=self._timeout
            )
            return response.status_code == 200
        except requests.RequestException:
            return False

    def fetch_attendance_logs(self) -> list[RawAttendanceLog]:
        """Download and map recent access-control events from the device.

        Note:
            Hikvision access-control events do not natively distinguish
            check-in from check-out the way ZKTeco's status codes do;
            every event is reported as :attr:`~models.enums.PunchType.CHECK_IN`.
            The service layer's daily aggregation (first-in/last-out
            over the day's punches) tolerates all-check-in input
            gracefully — it simply treats the earliest as the check-in
            and, if only one punch exists, records no check-out.
        """
        session = self._get_session()
        payload = {
            "AcsEventCond": {
                "searchID": "1",
                "searchResultPosition": 0,
                "maxResults": 200,
                "major": _ACS_EVENT_MAJOR_ACCESS_CONTROL,
                "minor": 0,
            }
        }
        try:
            response = session.post(
                f"{self._base_url}/ISAPI/AccessControl/AcsEvent?format=json",
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise DeviceConnectionError(f"Failed to fetch attendance logs: {exc}") from exc

        logs: list[RawAttendanceLog] = []
        for event in data.get("AcsEvent", {}).get("InfoList", []):
            employee_no = event.get("employeeNoString") or event.get("employeeNo")
            time_str = event.get("time")
            if not employee_no or not time_str:
                continue
            punch_time = datetime.fromisoformat(time_str).astimezone(timezone.utc)
            logs.append(
                RawAttendanceLog(
                    device_user_reference=str(employee_no),
                    punch_type=PunchType.CHECK_IN,
                    punch_time=punch_time,
                )
            )
        return logs

    def fetch_users(self) -> list[RawDeviceUser]:
        """Download every user currently enrolled on the device."""
        session = self._get_session()
        payload = {
            "UserInfoSearchCond": {
                "searchID": "1",
                "searchResultPosition": 0,
                "maxResults": 200,
            }
        }
        try:
            response = session.post(
                f"{self._base_url}/ISAPI/AccessControl/UserInfo/Search?format=json",
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise DeviceConnectionError(f"Failed to fetch users: {exc}") from exc

        users: list[RawDeviceUser] = []
        for entry in data.get("UserInfoSearch", {}).get("UserInfo", []):
            employee_no = entry.get("employeeNo")
            if employee_no:
                users.append(
                    RawDeviceUser(
                        device_user_reference=str(employee_no), name=entry.get("name", "")
                    )
                )
        return users

    def push_users(self, users: list[RawDeviceUser]) -> None:
        """Enroll or update users on the device."""
        session = self._get_session()
        for user in users:
            payload = {
                "UserInfo": {
                    "employeeNo": user.device_user_reference,
                    "name": user.name,
                    "userType": "normal",
                    "Valid": {"enable": True},
                }
            }
            try:
                response = session.post(
                    f"{self._base_url}/ISAPI/AccessControl/UserInfo/Record?format=json",
                    json=payload,
                    timeout=self._timeout,
                )
                response.raise_for_status()
            except requests.RequestException as exc:
                raise DeviceConnectionError(
                    f"Failed to push user {user.device_user_reference!r}: {exc}"
                ) from exc

    def disconnect(self) -> None:
        """Close the underlying HTTP session, if one is open."""
        if self._session is not None:
            self._session.close()
            self._session = None

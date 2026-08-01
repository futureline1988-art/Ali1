"""Resolves a :class:`~models.device.Device` to a live protocol connector.

The only piece of code in this project that maps
:class:`~models.enums.DeviceProtocol` to a concrete
:class:`~devices.device_interface.DeviceConnector` implementation —
adding a new protocol later means adding one branch here and a new
connector class, not touching ``services/device_service.py``.
"""

from __future__ import annotations

from config import get_config
from devices.device_interface import DeviceConnector
from devices.hikvision_device import HikvisionConnector
from devices.zkteco_device import ZKTecoConnector
from models.device import Device
from models.enums import DeviceProtocol

#: Hikvision's ISAPI needs a username as well as a password; this
#: project only has one `communication_key` string field on Device, so
#: Hikvision devices store it as "username:password". A key with no
#: colon is treated as the password alone, with this default username.
_DEFAULT_HIKVISION_USERNAME = "admin"


def _parse_hikvision_credentials(communication_key: str | None) -> tuple[str, str]:
    """Split a Device's communication_key into (username, password) for Hikvision.

    Args:
        communication_key: The device's stored key, expected to be
            ``"username:password"`` (or just a password, using the
            default username).

    Returns:
        A ``(username, password)`` tuple.
    """
    if not communication_key:
        return _DEFAULT_HIKVISION_USERNAME, ""
    if ":" in communication_key:
        username, _, password = communication_key.partition(":")
        return username or _DEFAULT_HIKVISION_USERNAME, password
    return _DEFAULT_HIKVISION_USERNAME, communication_key


class UnsupportedProtocolError(Exception):
    """Raised when a device's protocol has no registered connector."""


class DeviceManager:
    """Factory that builds the right connector for a device's protocol."""

    def get_connector(self, device: Device) -> DeviceConnector:
        """Build a connector for ``device``, based on its configured protocol.

        Args:
            device: The device to connect to.

        Returns:
            A connector ready to use (not yet connected — each
            connector method manages its own connection lifecycle).

        Raises:
            UnsupportedProtocolError: If ``device.protocol`` has no
                registered connector implementation.
        """
        device_defaults = get_config().device

        if device.protocol is DeviceProtocol.ZKTECO_TCP:
            return ZKTecoConnector(
                host=device.host,
                port=device.port,
                password=device.communication_key,
                timeout=device.timeout_seconds
                or device_defaults.zkteco_default_timeout_seconds,
                force_udp=False,
            )
        if device.protocol is DeviceProtocol.ZKTECO_UDP:
            return ZKTecoConnector(
                host=device.host,
                port=device.port,
                password=device.communication_key,
                timeout=device.timeout_seconds
                or device_defaults.zkteco_default_timeout_seconds,
                force_udp=True,
            )
        if device.protocol is DeviceProtocol.HIKVISION:
            username, password = _parse_hikvision_credentials(device.communication_key)
            return HikvisionConnector(
                host=device.host,
                port=device.port,
                username=username,
                password=password,
                timeout=device.timeout_seconds
                or device_defaults.hikvision_default_timeout_seconds,
            )

        raise UnsupportedProtocolError(
            f"No connector registered for protocol {device.protocol.value!r}"
        )

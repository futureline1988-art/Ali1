"""Device profile ORM model — a reusable biometric-device connection template.

Reuses :class:`~models.enums.DeviceProtocol` directly (the same enum
:class:`~models.device.Device` uses) rather than defining a second,
parallel protocol enum.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from developer_suite.database.base import DeveloperSuiteBaseModel
from models.base import enum_column_type
from models.enums import DeviceProtocol


class DeviceProfile(DeveloperSuiteBaseModel):
    """A reusable set of biometric-device connection defaults.

    Attributes:
        name: A unique, human-readable identifier for this profile.
        protocol: Which :class:`~models.enums.DeviceProtocol` this
            profile targets.
        default_port: Default TCP/UDP port for that protocol (e.g.
            ``4370``, ZKTeco's well-known default).
        timeout_seconds: Default connection timeout, matching
            :attr:`~models.company_settings.CompanySettings.default_device_timeout_seconds`'s
            default.
        sync_interval_minutes: Default interval between sync runs,
            matching
            :attr:`~models.company_settings.CompanySettings.default_sync_interval_minutes`'s
            default.
        auto_reconnect: Whether a device should be automatically
            retried after a dropped connection.
    """

    name: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    protocol: Mapped[DeviceProtocol] = mapped_column(
        enum_column_type(DeviceProtocol), default=DeviceProtocol.ZKTECO_TCP, nullable=False
    )
    default_port: Mapped[int] = mapped_column(Integer, default=4370, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    sync_interval_minutes: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    auto_reconnect: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

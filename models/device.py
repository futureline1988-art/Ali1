"""Biometric device registry ORM model.

Stores the connection details and last-known state of every fingerprint
device a company has registered. The actual protocol implementations
(ZKTeco TCP/IP, ZKTeco UDP, Hikvision) live behind an interface in the
``devices/`` package and know nothing about this model; this module only
persists what a device *is* and its last observed status, never how to
talk to it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import BaseModel, CompanyScopedMixin, UTCDateTime, enum_column_type
from models.enums import DeviceProtocol, DeviceStatus


class Device(CompanyScopedMixin, BaseModel):
    """A registered biometric attendance device.

    Attributes:
        branch_id: The branch this device is physically installed at, if
            the company tracks that.
        name: Friendly device name (e.g. ``"بصمة المدخل الرئيسي"``);
            unique within the owning company.
        protocol: Which :class:`~models.enums.DeviceProtocol` this device
            speaks.
        host: IP address or hostname used to reach the device.
        port: TCP/UDP port used to reach the device.
        serial_number: Manufacturer serial number, if known.
        communication_key: A device-level shared secret/password some
            protocols require (e.g. a ZKTeco "comm key"). Stored as
            whatever string the service layer provides — this model has
            no opinion on encryption, but callers should encrypt it at
            rest via ``utils/security.py`` before persisting, since,
            unlike a user password, it must remain recoverable in order
            to actually authenticate to the device.
        status: Last-known :class:`~models.enums.DeviceStatus`.
        last_seen_at: Timestamp of the last successful connectivity
            check (test connection / heartbeat), regardless of whether a
            data sync happened.
        last_sync_at: Timestamp of the last successful attendance-log or
            user-data synchronization.
        is_active: Whether this device should be included in scheduled
            sync jobs.
        timeout_seconds: Per-device connection timeout override; falls
            back to ``config.DeviceConfig`` protocol defaults when
            ``NULL``.
        notes: Free-form notes.
        branch: The related :class:`~models.branch.Branch`, if any.
    """

    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_devices_company_id_name"),
        UniqueConstraint("company_id", "host", "port", name="uq_devices_company_id_host_port"),
        CheckConstraint("port > 0 AND port <= 65535", name="ck_devices_port_range"),
    )

    branch_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("branches.id", ondelete="SET NULL"), nullable=True, index=True
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    protocol: Mapped[DeviceProtocol] = mapped_column(
        enum_column_type(DeviceProtocol), nullable=False, index=True
    )
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    serial_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    communication_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[DeviceStatus] = mapped_column(
        enum_column_type(DeviceStatus), nullable=False, default=DeviceStatus.UNKNOWN, index=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    branch: Mapped["Branch | None"] = relationship("Branch")  # noqa: F821

    @property
    def uses_zkteco_protocol(self) -> bool:
        """Whether this device speaks either ZKTeco variant."""
        return self.protocol in (DeviceProtocol.ZKTECO_TCP, DeviceProtocol.ZKTECO_UDP)

    @property
    def protocol_label_ar(self) -> str:
        """Arabic display label for :attr:`protocol`."""
        return self.protocol.label_ar

    @property
    def protocol_label_en(self) -> str:
        """English display label for :attr:`protocol`."""
        return self.protocol.label_en

    @property
    def status_label_ar(self) -> str:
        """Arabic display label for :attr:`status`."""
        return self.status.label_ar

    @property
    def status_label_en(self) -> str:
        """English display label for :attr:`status`."""
        return self.status.label_en

    def mark_online(self) -> None:
        """Record a successful connectivity check."""
        self.status = DeviceStatus.ONLINE
        self.last_seen_at = datetime.now(timezone.utc)

    def mark_offline(self) -> None:
        """Record that the device could not be reached."""
        self.status = DeviceStatus.OFFLINE
        self.last_seen_at = datetime.now(timezone.utc)

    def mark_error(self) -> None:
        """Record that communication with the device failed abnormally."""
        self.status = DeviceStatus.ERROR
        self.last_seen_at = datetime.now(timezone.utc)

    def record_sync(self) -> None:
        """Stamp :attr:`last_sync_at` after a successful data sync."""
        self.last_sync_at = datetime.now(timezone.utc)

    def __repr__(self) -> str:
        """Return a concise, debugger-friendly representation."""
        return (
            f"<Device id={self.id!r} name={self.name!r} "
            f"protocol={self.protocol.value!r} host={self.host!r}:{self.port!r}>"
        )

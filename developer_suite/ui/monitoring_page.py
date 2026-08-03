"""Monitoring page: read-only operational visibility.

No remote actions of any kind — every control here is a "reload the
current read," never a write. Talks only to
:class:`~developer_suite.admin.client.AdminApiClient`.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from developer_suite.admin.client import (
    AdminApiClient,
    AdminApiError,
    AdminApiNotConfiguredError,
    DeviceInfo,
    SyncActivityEntry,
)

_DEVICE_COLUMNS = ("الاسم", "النوع", "الحالة", "آخر ظهور")
_REGISTRATION_COLUMNS = ("الاسم", "النوع", "الحالة", "تاريخ التسجيل")
_ACTIVITY_COLUMNS = ("المعرّف", "الكيان", "العملية", "الحالة", "التاريخ", "السبب")
_FAILURE_STATUSES = ("conflict", "rejected")
_MAX_REGISTRATIONS_SHOWN = 20

_DEVICE_TYPE_LABELS_AR = {
    "attendance_client": "نظام الحضور",
    "developer_suite": "مجموعة المطورين",
}


def _device_type_label(device_type: str) -> str:
    return _DEVICE_TYPE_LABELS_AR.get(device_type, device_type)


def _format_datetime(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "—"


def _build_table(columns: tuple[str, ...]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    return table


def _populate_devices(table: QTableWidget, devices: list[DeviceInfo]) -> None:
    table.setRowCount(len(devices))
    for row, device in enumerate(devices):
        table.setItem(row, 0, QTableWidgetItem(device.name))
        table.setItem(row, 1, QTableWidgetItem(_device_type_label(device.device_type)))
        table.setItem(row, 2, QTableWidgetItem("متصل" if device.is_online() else "غير متصل"))
        table.setItem(row, 3, QTableWidgetItem(_format_datetime(device.last_seen_at)))


def _populate_registrations(table: QTableWidget, devices: list[DeviceInfo]) -> None:
    recent = sorted(devices, key=lambda device: device.created_at, reverse=True)[:_MAX_REGISTRATIONS_SHOWN]
    table.setRowCount(len(recent))
    for row, device in enumerate(recent):
        table.setItem(row, 0, QTableWidgetItem(device.name))
        table.setItem(row, 1, QTableWidgetItem(_device_type_label(device.device_type)))
        table.setItem(row, 2, QTableWidgetItem("متصل" if device.is_online() else "غير متصل"))
        table.setItem(row, 3, QTableWidgetItem(_format_datetime(device.created_at)))


def _populate_activity(table: QTableWidget, entries: list[SyncActivityEntry]) -> None:
    table.setRowCount(len(entries))
    for row, entry in enumerate(entries):
        table.setItem(row, 0, QTableWidgetItem(str(entry.id)))
        table.setItem(row, 1, QTableWidgetItem(f"{entry.entity_type} / {entry.entity_id}"))
        table.setItem(row, 2, QTableWidgetItem(entry.operation))
        table.setItem(row, 3, QTableWidgetItem(entry.status))
        table.setItem(row, 4, QTableWidgetItem(_format_datetime(entry.created_at)))
        table.setItem(row, 5, QTableWidgetItem(entry.conflict_reason or ""))


class MonitoringPage(QWidget):
    """Read-only monitoring: device connectivity, recent registrations, and sync activity."""

    def __init__(self, admin_client: AdminApiClient, *, parent: QWidget | None = None) -> None:
        """Build the page and load the initial data.

        Args:
            admin_client: The read-only client every table's data
                comes from.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._client = admin_client

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("المراقبة", self)
        title_font = title.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        header.addWidget(title, stretch=1)

        self.refresh_button = QPushButton("تحديث", self)
        self.refresh_button.clicked.connect(self.reload)
        header.addWidget(self.refresh_button)
        outer.addLayout(header)

        self.message_label = QLabel(self)
        self.message_label.setWordWrap(True)
        outer.addWidget(self.message_label)

        self.tabs = QTabWidget(self)
        outer.addWidget(self.tabs)

        self.devices_table = _build_table(_DEVICE_COLUMNS)
        self.tabs.addTab(self.devices_table, "الأجهزة (متصلة/غير متصلة)")

        self.registrations_table = _build_table(_REGISTRATION_COLUMNS)
        self.tabs.addTab(self.registrations_table, "أحدث التسجيلات")

        self.activity_table = _build_table(_ACTIVITY_COLUMNS)
        self.tabs.addTab(self.activity_table, "أحدث الأنشطة")

        self.failures_table = _build_table(_ACTIVITY_COLUMNS)
        self.tabs.addTab(self.failures_table, "أخطاء المزامنة")

        self.reload()

    def reload(self) -> None:
        """Re-fetch every table's data from the Attendance Server."""
        self.message_label.setText("")

        try:
            devices = self._client.list_devices()
        except AdminApiNotConfiguredError:
            self.message_label.setText("لم يتم إعداد رمز الإدارة بعد؛ بيانات المراقبة غير متاحة.")
            devices = []
        except AdminApiError as exc:
            self.message_label.setText(f"تعذّر الاتصال بخادم الحضور: {exc}")
            devices = []

        _populate_devices(self.devices_table, devices)
        _populate_registrations(self.registrations_table, devices)

        try:
            activity = self._client.list_recent_activity(limit=100)
        except AdminApiError:
            activity = []

        _populate_activity(self.activity_table, activity)
        failures = [entry for entry in activity if entry.status in _FAILURE_STATUSES]
        _populate_activity(self.failures_table, failures)

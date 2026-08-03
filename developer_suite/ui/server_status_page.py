"""Server Status page: a read-only view of the Attendance Server's own health.

Talks only to :class:`~developer_suite.admin.client.AdminApiClient` —
never to the server's database or any repository directly. Purely a
read; no action this page could ever trigger touches the server.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from developer_suite.admin.client import AdminApiClient, AdminApiError, AdminApiNotConfiguredError
from developer_suite.config import DeveloperSuiteConfig


def _format_uptime(seconds: float) -> str:
    """Render a duration in seconds as a short Arabic "Xd Xh Xm" string."""
    total_seconds = int(seconds)
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days} يوم")
    if hours:
        parts.append(f"{hours} ساعة")
    if minutes:
        parts.append(f"{minutes} دقيقة")
    if not parts:
        parts.append(f"{secs} ثانية")
    return " ".join(parts)


def _clear_form(form: QFormLayout) -> None:
    while form.rowCount():
        form.removeRow(0)


class ServerStatusPage(QWidget):
    """A read-only snapshot of the Attendance Server's health."""

    def __init__(
        self,
        admin_client: AdminApiClient,
        config: DeveloperSuiteConfig,
        *,
        parent: QWidget | None = None,
    ) -> None:
        """Build the page and load the initial status.

        Args:
            admin_client: The read-only client every displayed value
                comes from.
            config: Supplies this installation's own version.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._client = admin_client
        self._config = config

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("حالة خادم الحضور", self)
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

        self.form = QFormLayout()
        outer.addLayout(self.form)
        outer.addStretch(1)

        self.reload()

    def reload(self) -> None:
        """Re-probe the server and repaint every field."""
        _clear_form(self.form)
        self.message_label.setText("")

        health_ok = self._client.check_health()
        version_info = self._client.get_version() if health_ok else None
        self.form.addRow(
            "صحة واجهة البرمجة (Health)", QLabel("سليمة" if health_ok else "غير متاحة", self)
        )
        self.form.addRow(
            "إصدار الخادم",
            QLabel(version_info.get("app_version", "غير معروف") if version_info else "غير معروف", self),
        )
        self.form.addRow("إصدار مجموعة المطورين (هذا التثبيت)", QLabel(self._config.app_version, self))

        try:
            status = self._client.get_server_status()
        except AdminApiNotConfiguredError:
            self.message_label.setText(
                "لم يتم إعداد رمز الإدارة بعد؛ لا يمكن عرض بيانات الحالة التفصيلية (اتصال قاعدة "
                "البيانات، مدة التشغيل، عدد التركيبات المتصلة)."
            )
            return
        except AdminApiError as exc:
            self.message_label.setText(f"تعذّر الاتصال بخادم الحضور: {exc}")
            return

        self.form.addRow(
            "اتصال قاعدة البيانات",
            QLabel("متصلة" if status.database_connected else "غير متصلة", self),
        )
        self.form.addRow("مدة التشغيل", QLabel(_format_uptime(status.uptime_seconds), self))

        try:
            devices = self._client.list_devices()
            connected_customers = sum(1 for device in devices if device.device_type == "attendance_client")
        except AdminApiError:
            connected_customers = None
        self.form.addRow(
            "عدد تركيبات نظام الحضور المتصلة",
            QLabel(str(connected_customers) if connected_customers is not None else "غير متاح", self),
        )

"""Monitoring module.

Empty in Phase 2. A later phase adds a dashboard over this
application's own database (total companies, active/expired licenses,
license expiration calendar) plus, once the sync layer exists,
online/offline status and last-synchronization display. No remote data
collection exists yet.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from developer_suite.modules._placeholder import build_placeholder_page
from developer_suite.modules.base import PlatformModule


class MonitoringModule(PlatformModule):
    """Placeholder implementation — no business logic yet."""

    @property
    def module_id(self) -> str:
        return "monitoring"

    @property
    def display_name_ar(self) -> str:
        return "المراقبة"

    @property
    def display_name_en(self) -> str:
        return "Monitoring"

    def build_page(self) -> QWidget:
        return build_placeholder_page(self.display_name_ar)

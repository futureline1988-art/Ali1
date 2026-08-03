"""Settings module.

Empty in Phase 12, like Remote Configuration and Update Manager were
in Phase 2 — a navigation destination reserved for this application's
own operator preferences (a later phase's concern), not the customer-
configuration templates :mod:`developer_suite.modules.remote_configuration`
already owns. No business logic exists here yet.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from developer_suite.modules._placeholder import build_placeholder_page
from developer_suite.modules.base import PlatformModule


class SettingsModule(PlatformModule):
    """Placeholder implementation — no business logic yet."""

    @property
    def module_id(self) -> str:
        return "settings"

    @property
    def display_name_ar(self) -> str:
        return "الإعدادات"

    @property
    def display_name_en(self) -> str:
        return "Settings"

    def build_page(self) -> QWidget:
        return build_placeholder_page(self.display_name_ar)

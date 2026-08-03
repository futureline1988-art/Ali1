"""Remote Configuration module.

Empty in Phase 2. A later phase (after the communication model in
``docs/PLATFORM_ARCHITECTURE_GAP_ANALYSIS.md``'s "Communication
Between Applications" section is approved) adds: per-company branding,
theme, language, and operational-setting profiles editable here and
pushed to a customer's Attendance Client. No communication with any
customer application exists yet.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from developer_suite.modules._placeholder import build_placeholder_page
from developer_suite.modules.base import PlatformModule


class RemoteConfigurationModule(PlatformModule):
    """Placeholder implementation — no business logic yet."""

    @property
    def module_id(self) -> str:
        return "remote_configuration"

    @property
    def display_name_ar(self) -> str:
        return "الإعدادات عن بُعد"

    @property
    def display_name_en(self) -> str:
        return "Remote Configuration"

    def build_page(self) -> QWidget:
        return build_placeholder_page(self.display_name_ar)

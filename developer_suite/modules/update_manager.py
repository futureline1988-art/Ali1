"""Update Manager module.

Empty in Phase 2. A later phase (depending on the sync layer) adds
"force update" distribution to customer installations. No remote
administration exists yet.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from developer_suite.modules._placeholder import build_placeholder_page
from developer_suite.modules.base import PlatformModule


class UpdateManagerModule(PlatformModule):
    """Placeholder implementation — no business logic yet."""

    @property
    def module_id(self) -> str:
        return "update_manager"

    @property
    def display_name_ar(self) -> str:
        return "إدارة التحديثات"

    @property
    def display_name_en(self) -> str:
        return "Update Manager"

    def build_page(self) -> QWidget:
        return build_placeholder_page(self.display_name_ar)

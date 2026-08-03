"""Customer Management module.

Empty in Phase 2. A later phase adds: create/edit/delete customer,
search, company information (name, phone, email, notes) — all stored
in the Developer Suite's own database (see this package's parent
``__init__.py`` for the ownership boundary: customer *accounts*, not
customer *operational* data).
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from developer_suite.modules._placeholder import build_placeholder_page
from developer_suite.modules.base import PlatformModule


class CustomerManagementModule(PlatformModule):
    """Placeholder implementation — no business logic yet."""

    @property
    def module_id(self) -> str:
        return "customer_management"

    @property
    def display_name_ar(self) -> str:
        return "إدارة العملاء"

    @property
    def display_name_en(self) -> str:
        return "Customer Management"

    def build_page(self) -> QWidget:
        return build_placeholder_page(self.display_name_ar)

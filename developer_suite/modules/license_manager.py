"""License Manager module.

Empty in Phase 2. A later phase adds: generate/renew/reissue/revoke
licenses (Trial/Monthly/Yearly/Lifetime), machine binding, Excel
export/import, database backup/restore — built on
:mod:`licensing.crypto.signing` (Phase 1) for the actual Ed25519
signing, and this application's own database for issued-license
history. No licensing changes happen in this phase — see this
package's parent ``__init__.py``.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from developer_suite.modules._placeholder import build_placeholder_page
from developer_suite.modules.base import PlatformModule


class LicenseManagerModule(PlatformModule):
    """Placeholder implementation — no business logic yet."""

    @property
    def module_id(self) -> str:
        return "license_manager"

    @property
    def display_name_ar(self) -> str:
        return "إدارة التراخيص"

    @property
    def display_name_en(self) -> str:
        return "License Manager"

    def build_page(self) -> QWidget:
        return build_placeholder_page(self.display_name_ar)

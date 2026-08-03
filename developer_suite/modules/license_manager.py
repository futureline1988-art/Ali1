"""License Manager module.

Phase 4: issue/renew/revoke licenses (Trial/Monthly/Yearly/Lifetime),
search, and expiration calculation — all built on
:mod:`licensing.license_generator`/:mod:`licensing.license_key`
(Phase 1's foundation, and the same signing/encoding code the vendor's
offline CLI already uses) via
:mod:`developer_suite.services.license_service`. No remote
administration, synchronization, or update delivery yet — see this
package's parent ``__init__.py``.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from developer_suite.modules.base import PlatformModule
from developer_suite.services.customer_service import CustomerService
from developer_suite.services.license_service import LicenseService
from developer_suite.ui.license_management_page import LicenseManagementPage


class LicenseManagerModule(PlatformModule):
    """License issuance, renewal, revocation, and search."""

    def __init__(self, license_service: LicenseService, customer_service: CustomerService) -> None:
        """Create the module bound to a license service and a customer service.

        Args:
            license_service: Performs every license operation this
                module's page needs.
            customer_service: Used only to populate the "issue new
                license" customer picker.
        """
        self._license_service = license_service
        self._customer_service = customer_service

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
        return LicenseManagementPage(self._license_service, self._customer_service)

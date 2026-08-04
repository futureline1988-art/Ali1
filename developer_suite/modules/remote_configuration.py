"""Remote Configuration module.

Phase 5: theme/print/attendance-policy/device/backup profile
templates, and named bundles composing one of each, stored and edited
entirely inside the Developer Suite via
:mod:`developer_suite.services.configuration_service`. Phase 13 adds
real publishing to a customer's Attendance Client installation
(:mod:`developer_suite.services.configuration_publish_service`), with
version history, pending-change comparison, and rollback — see
:mod:`developer_suite.ui.configuration_publish_panel`.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from developer_suite.admin.client import AdminApiClient
from developer_suite.admin.session_manager import AdminSessionManager
from developer_suite.modules.base import PlatformModule
from developer_suite.services.configuration_publish_service import ConfigurationPublishService
from developer_suite.services.configuration_service import ConfigurationService
from developer_suite.services.customer_service import CustomerService
from developer_suite.ui.configuration_editor_page import ConfigurationEditorPage


class RemoteConfigurationModule(PlatformModule):
    """Theme, print, attendance-policy, device, and backup profile templates, plus publishing."""

    def __init__(
        self,
        configuration_service: ConfigurationService,
        publish_service: ConfigurationPublishService,
        customer_service: CustomerService,
        admin_client: AdminApiClient,
        admin_session_manager: AdminSessionManager,
    ) -> None:
        """Create the module bound to its dependencies.

        Args:
            configuration_service: Performs every profile/bundle
                operation this module's page needs.
            publish_service: Performs every publish/compare/rollback
                operation the publishing tab needs.
            customer_service: Populates the publishing tab's customer
                picker.
            admin_client: Populates the publishing tab's target
                -installation picker.
            admin_session_manager: Supplies the current administrator's
                identity to the publishing tab.
        """
        self._configuration_service = configuration_service
        self._publish_service = publish_service
        self._customer_service = customer_service
        self._admin_client = admin_client
        self._admin_session_manager = admin_session_manager

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
        return ConfigurationEditorPage(
            self._configuration_service,
            self._publish_service,
            self._customer_service,
            self._admin_client,
            self._admin_session_manager,
        )

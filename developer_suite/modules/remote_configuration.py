"""Remote Configuration module.

Phase 5: theme/print/attendance-policy/device/backup profile
templates, and named bundles composing one of each, stored and edited
entirely inside the Developer Suite via
:mod:`developer_suite.services.configuration_service`. No network
code, synchronization, deployment, or communication with any customer
application exists yet — this phase only defines how these templates
are stored and edited (see ``docs/PLATFORM_ARCHITECTURE_GAP_ANALYSIS.md``'s
"Communication Between Applications" section for what a later,
not-yet-approved phase would add).
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from developer_suite.modules.base import PlatformModule
from developer_suite.services.configuration_service import ConfigurationService
from developer_suite.ui.configuration_editor_page import ConfigurationEditorPage


class RemoteConfigurationModule(PlatformModule):
    """Theme, print, attendance-policy, device, and backup profile templates."""

    def __init__(self, configuration_service: ConfigurationService) -> None:
        """Create the module bound to a configuration service.

        Args:
            configuration_service: Performs every profile/bundle
                operation this module's page needs.
        """
        self._configuration_service = configuration_service

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
        return ConfigurationEditorPage(self._configuration_service)

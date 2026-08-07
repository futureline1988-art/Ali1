"""Device Diagnostics module: safe, read-only network investigation of an
attendance device with no known protocol/connector yet (see
``developer_suite/ui/device_diagnostics_page.py`` for the full
rationale). No dependency on the Attendance Server admin API -- this
runs entirely locally against whatever IP the operator enters.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from developer_suite.config import DeveloperSuiteConfig
from developer_suite.modules.base import PlatformModule
from developer_suite.ui.device_diagnostics_page import DeviceDiagnosticsPage


class DeviceDiagnosticsModule(PlatformModule):
    """Safe, protocol-agnostic network diagnostic for an unrecognized device."""

    def __init__(self, config: DeveloperSuiteConfig) -> None:
        """Create the module bound to its one dependency.

        Args:
            config: Supplies the writable directory diagnostic reports
                are saved into.
        """
        self._config = config

    @property
    def module_id(self) -> str:
        return "device_diagnostics"

    @property
    def display_name_ar(self) -> str:
        return "تشخيص الأجهزة"

    @property
    def display_name_en(self) -> str:
        return "Device Diagnostics"

    def build_page(self) -> QWidget:
        return DeviceDiagnosticsPage(self._config)

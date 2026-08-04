"""Reporting & Analytics module.

Phase 15: executive, customer, license, synchronization, update
-deployment, audit log, device, and configuration-publication-history
reports — read-only, filterable, chartable, and exportable to PDF/
Excel/CSV — see :mod:`developer_suite.ui.reporting_page`.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from developer_suite.config import DeveloperSuiteConfig
from developer_suite.modules.base import PlatformModule
from developer_suite.services.reporting_service import ReportingService
from developer_suite.ui.reporting_page import ReportingPage


class ReportingModule(PlatformModule):
    """Build, filter, chart, and export every Phase 15 report category."""

    def __init__(self, reporting_service: ReportingService, config: DeveloperSuiteConfig) -> None:
        """Create the module bound to its dependencies.

        Args:
            reporting_service: Assembles every report this module's
                page displays.
            config: Supplies the PDF exporter's bundled font
                directory.
        """
        self._reporting_service = reporting_service
        self._config = config

    @property
    def module_id(self) -> str:
        return "reporting"

    @property
    def display_name_ar(self) -> str:
        return "التقارير والتحليلات"

    @property
    def display_name_en(self) -> str:
        return "Reporting & Analytics"

    def build_page(self) -> QWidget:
        return ReportingPage(self._reporting_service, self._config)

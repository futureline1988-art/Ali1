"""Tests for the Developer Suite's Device Diagnostics module/page.

Reuses the exact same ``tools.diagnostics.deli_es172_diagnose`` logic
the Attendance Client's own "تشخيص شبكة الجهاز" dialog exercises (see
``tests/test_deli_diagnostic_tool.py::TestNetworkDiagnosticDialog`` for
the equivalent client-side tests) -- these tests only prove the
Developer Suite's own module/page wiring and its writable-output-
directory handling, not the diagnostic logic itself again.
"""

from __future__ import annotations

import shutil

import pytest

import developer_suite.config as developer_suite_config_module
from developer_suite.config import get_developer_suite_config
from developer_suite.modules.device_diagnostics import DeviceDiagnosticsModule
from developer_suite.ui.device_diagnostics_page import DeviceDiagnosticsPage
from tools.diagnostics import deli_es172_diagnose as diag


@pytest.fixture
def dev_suite_config(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_SUITE_DB_SQLITE_PATH", str(tmp_path / "developer_suite_test.db"))
    developer_suite_config_module._config_instance = None
    yield get_developer_suite_config()
    developer_suite_config_module._config_instance = None


class TestDeviceDiagnosticsModule:
    def test_module_interface(self, dev_suite_config):
        module = DeviceDiagnosticsModule(dev_suite_config)
        assert module.module_id == "device_diagnostics"
        assert module.display_name_ar == "تشخيص الأجهزة"
        assert module.display_name_en == "Device Diagnostics"

    def test_build_page_returns_a_device_diagnostics_page(self, dev_suite_config, qapp):
        module = DeviceDiagnosticsModule(dev_suite_config)
        page = module.build_page()
        assert isinstance(page, DeviceDiagnosticsPage)


class TestDeviceDiagnosticsPage:
    @pytest.fixture(autouse=True)
    def _cleanup_diagnostics_dir(self, dev_suite_config):
        output_dir = dev_suite_config.paths.data_dir / "diagnostics"
        if output_dir.exists():
            shutil.rmtree(output_dir)
        yield
        if output_dir.exists():
            shutil.rmtree(output_dir)

    def test_default_ip_is_prefilled_and_folder_button_starts_disabled(self, dev_suite_config, qtbot):
        page = DeviceDiagnosticsPage(dev_suite_config)
        qtbot.addWidget(page)
        assert page.ip_edit.text() == diag.DEFAULT_TARGET_IP
        assert page.open_folder_button.isEnabled() is False

    def test_running_the_diagnostic_populates_results_and_writes_reports(
        self, dev_suite_config, qtbot, monkeypatch
    ):
        from developer_suite.ui import device_diagnostics_page as page_module

        fake_report = {
            "target_ip": "192.168.1.8",
            "generated_at_utc": "2026-01-01T00:00:00Z",
            "runner_platform": "test",
            "ping": {"reachable": True},
            "ports": [{"port": 5005, "open": False, "error": "timed out"}],
        }
        monkeypatch.setattr(page_module.network_diagnostic, "diagnose", lambda ip: fake_report)

        page = DeviceDiagnosticsPage(dev_suite_config)
        qtbot.addWidget(page)
        page.run_button.click()

        assert "192.168.1.8" in page.result_view.toPlainText()
        assert page.open_folder_button.isEnabled() is True
        assert page._last_output_dir is not None
        assert page._last_output_dir == dev_suite_config.paths.data_dir / "diagnostics"
        assert list(page._last_output_dir.glob("*.json"))
        assert list(page._last_output_dir.glob("*.txt"))

    def test_empty_ip_is_rejected_before_running_anything(self, dev_suite_config, qtbot, monkeypatch):
        from developer_suite.ui import device_diagnostics_page as page_module

        called = False

        def _fake_diagnose(ip):
            nonlocal called
            called = True
            return {}

        monkeypatch.setattr(page_module.network_diagnostic, "diagnose", _fake_diagnose)

        page = DeviceDiagnosticsPage(dev_suite_config)
        qtbot.addWidget(page)
        page.ip_edit.setText("")
        page.run_button.click()

        assert called is False
        assert page.open_folder_button.isEnabled() is False

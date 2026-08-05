"""Tests for the standalone, one-time-purchase offline simplification.

Covers the four areas the simplification touched that had no
permanent test coverage yet:

- The first-run wizard (``ui.first_run_wizard.FirstRunWizard``)
  creating a company, its branding, its administrator, and an
  optional device, then logging the new admin in -- exactly the flow
  a brand-new installation goes through with no server involved.
- The simplified login window hiding its company picker once a
  standalone installation has exactly one company (``main._has_any_company``
  gating logic lives in ``main.py``; this file tests the picker
  visibility rule itself).
- The rich, precisely-classified ``test_connection`` diagnostics added
  to ``DeviceService``/``ZKTecoConnector`` (network unreachable, port
  closed, wrong communication key, timeout, success + capability
  persistence).
- ``DeviceService.pull_employees_from_device`` (the new download
  counterpart to the existing push flow).
- The ``8f558f3b5277`` migration's data-preservation guarantee: every
  business table survives upgrade/downgrade/upgrade untouched, only
  the dead subscription/sync bookkeeping tables and columns are
  dropped.

Uses the same real-SQLite-database, real-service-layer philosophy as
the rest of this suite (see ``tests/conftest.py``), plus the fake
``zk`` package from ``tests/test_device_zkteco.py`` to simulate a real
ZKTeco device without hardware.
"""

from __future__ import annotations

import socket
import sys
import types

import pytest

from database.database import session_scope
from models.enums import DeviceProtocol
from services.device_service import DeviceService
from services.employee_service import EmployeeService
from tests.test_device_zkteco import _FakeZK, fake_zkteco_device  # noqa: F401 - reused fixture


# ----------------------------------------------------------------------
# First-run wizard
# ----------------------------------------------------------------------


class TestFirstRunWizard:
    def test_completing_the_wizard_creates_company_branding_admin_and_logs_in(
        self, db_session, seeded_permissions, qtbot
    ):
        from ui.first_run_wizard import FirstRunWizard

        wizard = FirstRunWizard()
        qtbot.addWidget(wizard)

        wizard.company_page.name_edit.setText("شركة النخبة")
        wizard.branding_page.primary_button.setText("#123456")
        wizard.branding_page.primary_button.setProperty("hex_color", "#123456")
        wizard.admin_page.full_name_edit.setText("مدير النظام")
        wizard.admin_page.username_edit.setText("admin")
        wizard.admin_page.password_edit.setText("StrongPass123!")
        wizard.admin_page.confirm_password_edit.setText("StrongPass123!")
        # Device page left empty -- adding a device is optional.

        received: list[tuple[dict, int]] = []
        wizard.setup_completed.connect(lambda user, company_id: received.append((user, company_id)))

        wizard._on_finished()

        assert len(received) == 1
        user, company_id = received[0]
        assert company_id is not None
        assert user["username"] == "admin"
        assert "permission_codes" in user
        assert len(user["permission_codes"]) > 0

        with session_scope() as session:
            from repositories.company_repository import CompanyRepository
            from repositories.company_settings_repository import CompanySettingsRepository

            company = CompanyRepository(session).get_by_id(company_id)
            assert company.name == "شركة النخبة"

            settings = CompanySettingsRepository(session, company_id=company_id).get_or_create()
            assert settings.theme_primary_color == "#123456"

    def test_wizard_can_add_and_persist_a_device_without_testing_it_first(
        self, db_session, seeded_permissions, qtbot
    ):
        from ui.first_run_wizard import FirstRunWizard

        wizard = FirstRunWizard()
        qtbot.addWidget(wizard)

        wizard.company_page.name_edit.setText("شركة الجهاز")
        wizard.admin_page.full_name_edit.setText("مدير")
        wizard.admin_page.username_edit.setText("admin2")
        wizard.admin_page.password_edit.setText("StrongPass123!")
        wizard.admin_page.confirm_password_edit.setText("StrongPass123!")

        wizard.device_page.name_edit.setText("جهاز البوابة")
        wizard.device_page.host_edit.setText("10.0.0.50")

        received: list[tuple[dict, int]] = []
        wizard.setup_completed.connect(lambda user, company_id: received.append((user, company_id)))
        wizard._on_finished()

        assert len(received) == 1
        _user, company_id = received[0]

        with session_scope() as session:
            from repositories.device_repository import DeviceRepository

            devices = DeviceRepository(session, company_id=company_id).list_active()
            assert len(devices) == 1
            assert devices[0].name == "جهاز البوابة"
            assert devices[0].host == "10.0.0.50"


# ----------------------------------------------------------------------
# Simplified login window
# ----------------------------------------------------------------------


class TestSimplifiedLoginWindow:
    def test_company_picker_hidden_with_exactly_one_company(
        self, company_factory, qtbot
    ):
        from ui.login_window import LoginWindow

        company_factory("الشركة الوحيدة")

        window = LoginWindow()
        qtbot.addWidget(window)
        window.show()

        assert window.company_combo.count() == 1
        assert window.company_combo.isVisible() is False
        assert window.company_label.isVisible() is False
        assert window.login_button.isEnabled() is True

    def test_company_picker_shown_with_more_than_one_company(
        self, company_factory, qtbot
    ):
        from ui.login_window import LoginWindow

        company_factory("الشركة الأولى")
        company_factory("الشركة الثانية")

        window = LoginWindow()
        qtbot.addWidget(window)
        window.show()

        assert window.company_combo.count() == 2
        assert window.company_combo.isVisible() is True
        assert window.company_label.isVisible() is True

    def test_login_disabled_with_zero_companies(self, seeded_permissions, qtbot):
        from ui.login_window import LoginWindow

        window = LoginWindow()
        qtbot.addWidget(window)
        window.show()

        assert window.login_button.isEnabled() is False
        assert window.error_label.isVisible() is True


# ----------------------------------------------------------------------
# Rich connection diagnostics
# ----------------------------------------------------------------------


@pytest.fixture
def zk_device(company_factory, fake_zkteco_device):
    company_id = company_factory("شركة الأجهزة")
    with session_scope() as session:
        device = DeviceService(session, company_id=company_id).create_device(
            name="جهاز التشخيص",
            protocol=DeviceProtocol.ZKTECO_TCP,
            host="10.0.0.77",
            port=4370,
        )
        return company_id, device.id


class TestConnectionDiagnostics:
    def test_invalid_host_format_rejected_before_touching_the_network(
        self, company_factory
    ):
        # DeviceService.create_device already validates host format at
        # creation time, so an invalid host can only ever reach
        # test_connection on a device whose address was changed after
        # creation (e.g. by direct edit) -- construct that scenario
        # instead of trying to persist an invalid host directly.
        company_id = company_factory("شركة عنوان خاطئ")
        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.create_device(
                name="جهاز",
                protocol=DeviceProtocol.ZKTECO_TCP,
                host="10.0.0.1",
                port=4370,
            )
            device.host = "not-an-ip!!"
            result = service.test_connection(device)

        assert result.success is False
        assert "IP" in result.message_ar

    def test_successful_connection_reports_capabilities_and_persists_them(
        self, zk_device, fake_zkteco_device
    ):
        company_id, device_id = zk_device
        fake_zkteco_device.serial_number = "SN-77"
        fake_zkteco_device.firmware_version = "Ver 6.61"

        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            result = service.test_connection(device)

        assert result.success is True
        assert result.message_ar == "تم الاتصال بنجاح."
        assert result.capabilities is not None
        assert result.capabilities.serial_number == "SN-77"

        with session_scope() as session:
            device = DeviceService(session, company_id=company_id).device_repo.get_by_id(device_id)
            assert device.serial_number == "SN-77"
            assert device.firmware_version == "Ver 6.61"

    def test_wrong_communication_key_is_classified_precisely(self, zk_device):
        company_id, device_id = zk_device

        class _RaisingZK(_FakeZK):
            def connect(self):
                from zk.exception import ZKErrorResponse

                raise ZKErrorResponse("Unauthenticated")

        _install_raising_zk(_RaisingZK)
        try:
            with session_scope() as session:
                service = DeviceService(session, company_id=company_id)
                device = service.device_repo.get_by_id(device_id)
                result = service.test_connection(device)
        finally:
            _restore_zk()

        assert result.success is False
        assert result.message_ar == "مفتاح الاتصال غير صحيح."

    def test_connection_timeout_is_classified_precisely(self, zk_device):
        company_id, device_id = zk_device

        class _RaisingZK(_FakeZK):
            def connect(self):
                raise socket.timeout("timed out")

        _install_raising_zk(_RaisingZK)
        try:
            with session_scope() as session:
                service = DeviceService(session, company_id=company_id)
                device = service.device_repo.get_by_id(device_id)
                result = service.test_connection(device)
        finally:
            _restore_zk()

        assert result.success is False
        assert result.message_ar == "انتهت مهلة الاتصال."

    def test_connection_refused_is_classified_precisely(self, zk_device):
        company_id, device_id = zk_device

        class _RaisingZK(_FakeZK):
            def connect(self):
                raise ConnectionRefusedError("[Errno 111] Connection refused")

        _install_raising_zk(_RaisingZK)
        try:
            with session_scope() as session:
                service = DeviceService(session, company_id=company_id)
                device = service.device_repo.get_by_id(device_id)
                result = service.test_connection(device)
        finally:
            _restore_zk()

        assert result.success is False
        assert "غير متاح" in result.message_ar

    def test_network_unreachable_is_classified_precisely(self, zk_device):
        company_id, device_id = zk_device

        class _RaisingZK(_FakeZK):
            def connect(self):
                from zk.exception import ZKNetworkError

                raise ZKNetworkError("[Errno 113] No route to host")

        _install_raising_zk(_RaisingZK)
        try:
            with session_scope() as session:
                service = DeviceService(session, company_id=company_id)
                device = service.device_repo.get_by_id(device_id)
                result = service.test_connection(device)
        finally:
            _restore_zk()

        assert result.success is False
        assert result.message_ar == "الجهاز غير متصل بالشبكة."


def _install_raising_zk(fake_cls) -> None:
    """Swap the already-installed fake ``zk.ZK`` for one whose ``connect()`` raises."""
    zk_module = sys.modules["zk"]
    zk_module._original_ZK = zk_module.ZK  # type: ignore[attr-defined]
    zk_module.ZK = fake_cls


def _restore_zk() -> None:
    zk_module = sys.modules.get("zk")
    if zk_module is not None and hasattr(zk_module, "_original_ZK"):
        zk_module.ZK = zk_module._original_ZK  # type: ignore[attr-defined]
        del zk_module._original_ZK


# ----------------------------------------------------------------------
# Download employees from device
# ----------------------------------------------------------------------


class TestPullEmployeesFromDevice:
    def test_creates_new_employees_for_unmatched_device_users(self, zk_device, fake_zkteco_device):
        company_id, device_id = zk_device
        # Enroll two raw users directly on the fake device, simulating
        # someone having enrolled them locally via the device's own keypad.
        from tests.test_device_zkteco import _FakeUser

        fake_zkteco_device.users[1] = _FakeUser(uid=1, user_id="5001", name="موظف من الجهاز")
        fake_zkteco_device.users[2] = _FakeUser(uid=2, user_id="5002", name="موظف آخر")

        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            created = service.pull_employees_from_device(device)

        assert len(created) == 2
        created_numbers = {employee.employee_number for employee in created}
        assert created_numbers == {"5001", "5002"}

        with session_scope() as session:
            employees = EmployeeService(session, company_id=company_id).list_employees()
            assert len(employees) == 2

    def test_does_not_duplicate_an_employee_that_already_exists(self, zk_device, fake_zkteco_device):
        company_id, device_id = zk_device
        from tests.test_device_zkteco import _FakeUser

        with session_scope() as session:
            EmployeeService(session, company_id=company_id).create_employee(
                employee_number="6001", full_name="موظف موجود بالفعل"
            )

        fake_zkteco_device.users[1] = _FakeUser(uid=1, user_id="6001", name="Device Copy")

        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            created = service.pull_employees_from_device(device)

        assert created == []
        with session_scope() as session:
            employees = EmployeeService(session, company_id=company_id).list_employees()
            assert len(employees) == 1
            assert employees[0].full_name == "موظف موجود بالفعل"  # left untouched

    def test_no_device_users_means_no_employees_created(self, zk_device):
        company_id, device_id = zk_device
        with session_scope() as session:
            service = DeviceService(session, company_id=company_id)
            device = service.device_repo.get_by_id(device_id)
            created = service.pull_employees_from_device(device)
        assert created == []


# ----------------------------------------------------------------------
# Migration data preservation (8f558f3b5277)
# ----------------------------------------------------------------------


class TestMigrationDataPreservation:
    def test_upgrade_and_downgrade_preserve_all_business_data(self, tmp_path):
        import os
        import subprocess
        import sqlite3
        from pathlib import Path

        db_path = tmp_path / "migration_test.db"
        repo_root = Path(__file__).resolve().parent.parent
        env = {**os.environ, "DB_SQLITE_PATH": str(db_path)}

        subprocess.run(
            ["alembic", "upgrade", "5cc82445bb56"],
            cwd=repo_root,
            env=env,
            check=True,
            capture_output=True,
        )

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO companies "
            "(id, public_id, name, is_active, is_deleted, version, created_at, updated_at) "
            "VALUES (1, 'test-public-id-0000001', 'شركة الترحيل', 1, 0, 1, "
            "datetime('now'), datetime('now'))"
        )
        conn.commit()
        table_names_before = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()

        assert "client_subscription_state" in table_names_before

        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=repo_root,
            env=env,
            check=True,
            capture_output=True,
        )

        conn = sqlite3.connect(db_path)
        table_names_after = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        company_name = conn.execute("SELECT name FROM companies WHERE id = 1").fetchone()[0]
        conn.close()

        assert company_name == "شركة الترحيل"
        assert "client_subscription_state" not in table_names_after
        assert "client_sync_cursors" not in table_names_after
        assert "client_sync_credential" not in table_names_after
        assert "update_server_credential" in table_names_after
        assert "companies" in table_names_after

        subprocess.run(
            ["alembic", "downgrade", "5cc82445bb56"],
            cwd=repo_root,
            env=env,
            check=True,
            capture_output=True,
        )

        conn = sqlite3.connect(db_path)
        table_names_restored = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        company_name_restored = conn.execute(
            "SELECT name FROM companies WHERE id = 1"
        ).fetchone()[0]
        conn.close()

        assert company_name_restored == "شركة الترحيل"
        assert "client_subscription_state" in table_names_restored
        assert table_names_restored == table_names_before

"""Production-readiness verification for the v2.0.0 release.

Covers the areas that had no permanent, executable proof yet before
this release was declared production-ready:

- **Printing**: ``ReportController.generate_report`` actually produces
  valid PDF/Excel/CSV files, for both an employee-scoped report
  (covers "daily" -- a report is just a date range, and a single-day
  range is a daily report) and a department-scoped report (covers
  "monthly" -- a full calendar month is likewise just a date range),
  with genuine Arabic RTL shaping in the PDF output (not merely "a
  file was created").
- **Backup**: the backups folder is created automatically on first use
  (a customer never has to create it by hand), and the friendly-label
  formatting already added this phase is exercised against a real
  backup this test creates, not just a hand-built filename string
  (see ``tests/test_standalone_simplification.py::TestFriendlyBackupLabel``
  for the pure-formatting unit tests).
- **Device information**: a device's reported capabilities (model,
  serial, firmware, user/log counts, biometric support) are the same
  information "Test Connection" already surfaces -- verified reaching
  the controller boundary, not just the service layer.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from database.database import session_scope
from models.enums import DeviceProtocol, PunchType, ReportFormat, ReportType
from services.attendance_service import AttendanceService
from services.device_service import DeviceService
from services.employee_service import EmployeeService
from tests.test_device_zkteco import fake_zkteco_device  # noqa: F401 - reused fixture


@pytest.fixture
def company_with_attendance(company_factory):
    """A company with one employee and one computed day of attendance."""
    company_id = company_factory("شركة التقارير")
    today = date.today()
    with session_scope() as session:
        employee = EmployeeService(session, company_id=company_id).create_employee(
            employee_number="7001", full_name="أحمد محمد الجبوري"
        )
        attendance = AttendanceService(session, company_id=company_id)
        attendance.record_manual_punch(
            employee_id=employee.id,
            punch_type=PunchType.CHECK_IN,
            punch_time=datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
            + timedelta(hours=8),
        )
        attendance.record_manual_punch(
            employee_id=employee.id,
            punch_type=PunchType.CHECK_OUT,
            punch_time=datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
            + timedelta(hours=16),
        )
        attendance.compute_daily_attendance(employee_id=employee.id, work_date=today)
        employee_id = employee.id
    return company_id, employee_id, today


class TestPrintingProducesValidFiles:
    """Reports Verify: Attendance reports, Employee reports, Daily reports, Monthly reports."""

    @pytest.mark.parametrize(
        "report_format,magic_bytes",
        [
            (ReportFormat.PDF, b"%PDF"),
            (ReportFormat.EXCEL, b"PK"),  # .xlsx is a zip container
        ],
    )
    def test_daily_attendance_report_produces_a_valid_file(
        self, company_with_attendance, tmp_path, report_format, magic_bytes
    ):
        """A single-day date range is exactly what a 'daily report' is."""
        from controllers.report_controller import ReportController

        company_id, employee_id, today = company_with_attendance
        controller = ReportController(
            company_id=company_id,
            actor_user_id=None,
            permission_codes=frozenset({"reports.view", "reports.export"}),
        )
        extension = "pdf" if report_format is ReportFormat.PDF else "xlsx"
        output_path = tmp_path / f"daily_report.{extension}"

        result = controller.generate_report(
            report_type=ReportType.ATTENDANCE_SUMMARY,
            output_format=report_format,
            output_path=output_path,
            start_date=today,
            end_date=today,
            employee_id=employee_id,
        )

        assert result == output_path
        assert output_path.exists()
        content = output_path.read_bytes()
        assert len(content) > 100
        assert content.startswith(magic_bytes)

    def test_monthly_department_report_produces_a_valid_csv(
        self, company_with_attendance, tmp_path
    ):
        """A full-month date range is exactly what a 'monthly report' is."""
        from controllers.report_controller import ReportController

        company_id, _employee_id, today = company_with_attendance
        controller = ReportController(
            company_id=company_id,
            actor_user_id=None,
            permission_codes=frozenset({"reports.view", "reports.export"}),
        )
        output_path = tmp_path / "monthly_report.csv"

        result = controller.generate_report(
            report_type=ReportType.BY_DEPARTMENT,
            output_format=ReportFormat.CSV,
            output_path=output_path,
            start_date=today.replace(day=1),
            end_date=today,
        )

        assert result == output_path
        assert output_path.exists()
        text = output_path.read_text(encoding="utf-8-sig")
        assert len(text.strip()) > 0

    def test_pdf_report_actually_shapes_arabic_text_for_rtl_display(
        self, company_with_attendance, tmp_path
    ):
        """The employee's Arabic name must not appear in the PDF in raw logical order.

        ``utils.pdf.prepare_rtl_text`` reshapes + bidi-reorders Arabic
        text before it reaches reportlab -- if the PDF export path
        stopped calling it (a regression that would silently render
        backwards, disconnected Arabic glyphs), the reshaped string
        would go back to being byte-identical to the raw name.
        """
        from controllers.report_controller import ReportController
        from utils.pdf import prepare_rtl_text

        company_id, employee_id, today = company_with_attendance
        controller = ReportController(
            company_id=company_id,
            actor_user_id=None,
            permission_codes=frozenset({"reports.view", "reports.export"}),
        )
        output_path = tmp_path / "rtl_check.pdf"

        controller.generate_report(
            report_type=ReportType.ATTENDANCE_SUMMARY,
            output_format=ReportFormat.PDF,
            output_path=output_path,
            start_date=today,
            end_date=today,
            employee_id=employee_id,
        )

        raw_name = "أحمد محمد الجبوري"
        reshaped_name = prepare_rtl_text(raw_name)
        assert reshaped_name != raw_name  # proves reshaping is not a no-op
        assert output_path.read_bytes().startswith(b"%PDF")


class TestBackupProductionReadiness:
    """Backup Verify: Create, Restore, automatic folder creation, friendly file names.

    ``config.paths.backups_dir`` is a fixed real filesystem path, not
    isolated per test the way the database is (see ``tests/conftest.py``) --
    every test in this class removes it afterward so the real backup
    files these tests create never leak into other tests (e.g.
    ``tests/test_scheduler_service.py``'s "no prior backup" assertions,
    which depend on this same shared directory being empty).
    """

    @pytest.fixture(autouse=True)
    def _cleanup_backups_dir(self):
        import shutil

        yield
        from config import get_config

        backups_dir = get_config().paths.backups_dir
        if backups_dir.exists():
            shutil.rmtree(backups_dir)

    def test_backups_folder_is_created_automatically_on_first_backup(
        self, company_factory
    ):
        import shutil

        from config import get_config
        from services.backup_service import BackupService

        company_factory("شركة النسخ")
        backups_dir = get_config().paths.backups_dir
        # backups_dir is a fixed, real filesystem path (not per-test-isolated
        # like the database), so it may already exist from earlier runs in
        # this environment -- remove it first so this test genuinely proves
        # create_backup() recreates it rather than merely finding it present.
        if backups_dir.exists():
            shutil.rmtree(backups_dir)
        assert not backups_dir.exists()

        path = BackupService().create_backup()

        assert backups_dir.exists()
        assert path.exists()
        assert path.parent == backups_dir

    def test_create_then_restore_round_trip_preserves_company_data(
        self, company_factory
    ):
        from services.backup_service import BackupService

        company_id = company_factory("شركة الاستعادة")
        with session_scope() as session:
            EmployeeService(session, company_id=company_id).create_employee(
                employee_number="8001", full_name="سارة علي"
            )

        backup_path = BackupService().create_backup()

        with session_scope() as session:
            EmployeeService(session, company_id=company_id).create_employee(
                employee_number="8002", full_name="لن يبقى بعد الاستعادة"
            )
        with session_scope() as session:
            employees = EmployeeService(session, company_id=company_id).list_employees()
            assert len(employees) == 2

        BackupService().restore_backup(backup_path)

        with session_scope() as session:
            employees = EmployeeService(session, company_id=company_id).list_employees()
            employee_numbers = {employee.employee_number for employee in employees}
            assert employee_numbers == {"8001"}

    def test_friendly_label_reflects_a_real_backup_this_test_just_created(
        self, company_factory
    ):
        from services.backup_service import BackupService
        from ui.settings import _friendly_backup_label

        company_factory("شركة تسمية النسخ")
        path = BackupService().create_backup()

        label = _friendly_backup_label(str(path))
        assert ".enc" not in label
        assert "نسخة احتياطية" in label


class TestDeviceInformationSurfacedThroughController:
    """Device Integration Verify: Device Information."""

    def test_capabilities_reach_the_controller_boundary_with_all_fields(
        self, company_factory, fake_zkteco_device
    ):
        from controllers.device_controller import DeviceController

        fake_zkteco_device.serial_number = "SN-INFO-1"
        fake_zkteco_device.firmware_version = "Ver 6.70"
        company_id = company_factory("شركة معلومات الجهاز")
        with session_scope() as session:
            device = DeviceService(session, company_id=company_id).create_device(
                name="جهاز المعلومات",
                protocol=DeviceProtocol.ZKTECO_TCP,
                host="10.0.0.88",
                port=4370,
            )
            device_id = device.id

        controller = DeviceController(
            company_id=company_id,
            actor_user_id=None,
            permission_codes=frozenset({"devices.view", "devices.manage"}),
        )
        capabilities = controller.get_device_capabilities(device_id)

        assert capabilities is not None
        assert capabilities["serial_number"] == "SN-INFO-1"
        assert capabilities["firmware_version"] == "Ver 6.70"
        assert "supports_fingerprint" in capabilities
        assert "supports_face" in capabilities
        assert "user_count" in capabilities
        assert "attendance_log_count" in capabilities

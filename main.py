"""Composition root: bootstraps logging, the database, and the Qt application.

Run directly (``python main.py``) to start the desktop application.
This module owns the process-wide startup sequence and the
login <-> main-window session lifecycle; it deliberately contains no
business logic of its own - every screen is built in ``ui/`` and every
operation goes through ``controllers/``.
"""

from __future__ import annotations

import sys
from types import TracebackType

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from config import get_config
from database.database import DatabaseConnectionError, get_database, session_scope
from licensing.license_service import LicenseService
from models.permission import Permission
from repositories.permission_repository import PermissionRepository
from ui.attendance import AttendancePage
from ui.dashboard_page import DashboardPage
from ui.departments import DepartmentsPage
from ui.devices import DevicesPage
from ui.employees import EmployeesPage
from ui.holidays import HolidaysPage
from ui.leave import LeavePage
from ui.license_window import LicenseActivationWindow
from ui.login_window import LoginWindow
from ui.main_window import MainWindow
from ui.reports import ReportsPage
from ui.settings import SettingsPage
from ui.shifts import ShiftsPage
from ui.theme import get_theme_manager
from ui.users import UsersPage
from ui.widgets import build_splash_screen
from utils.i18n import get_locale_manager
from utils.logger import logger, setup_logging
from utils.security import SessionManager

# (code, module, name_ar, name_en) - the global permission catalog. Shipped
# and maintained with the software itself (see models/permission.py's
# docstring), not created by tenants, so it is seeded once here at startup
# rather than through any company-scoped controller.
_DEFAULT_PERMISSIONS: list[tuple[str, str, str, str]] = [
    ("dashboard.view", "dashboard", "عرض لوحة التحكم", "View Dashboard"),
    ("employees.view", "employees", "عرض الموظفين", "View Employees"),
    ("employees.manage", "employees", "إدارة الموظفين", "Manage Employees"),
    ("departments.view", "departments", "عرض الأقسام", "View Departments"),
    ("departments.manage", "departments", "إدارة الأقسام", "Manage Departments"),
    ("attendance.view", "attendance", "عرض الحضور والانصراف", "View Attendance"),
    ("attendance.manage", "attendance", "إدارة الحضور والانصراف", "Manage Attendance"),
    ("devices.view", "devices", "عرض الأجهزة", "View Devices"),
    ("devices.manage", "devices", "إدارة الأجهزة", "Manage Devices"),
    ("shifts.view", "shifts", "عرض الورديات", "View Shifts"),
    ("shifts.manage", "shifts", "إدارة الورديات", "Manage Shifts"),
    ("holidays.view", "holidays", "عرض العطلات", "View Holidays"),
    ("holidays.manage", "holidays", "إدارة العطلات", "Manage Holidays"),
    ("leave.view", "leave", "عرض الإجازات", "View Leave"),
    ("leave.manage", "leave", "إدارة الإجازات", "Manage Leave"),
    ("reports.view", "reports", "عرض التقارير", "View Reports"),
    ("reports.export", "reports", "تصدير التقارير", "Export Reports"),
    ("users.view", "users", "عرض المستخدمين", "View Users"),
    ("users.manage", "users", "إدارة المستخدمين", "Manage Users"),
    ("roles.manage", "users", "إدارة الأدوار والصلاحيات", "Manage Roles & Permissions"),
    ("settings.view", "settings", "عرض الإعدادات", "View Settings"),
    ("settings.manage", "settings", "إدارة إعدادات الشركة", "Manage Company Settings"),
    ("backup.manage", "settings", "إدارة النسخ الاحتياطي", "Manage Backups"),
]


def _seed_default_permissions() -> None:
    """Seed the global permission catalog on first run.

    A no-op if the catalog already has any rows (e.g. every run after
    the first, or a database restored from a backup).
    """
    with session_scope() as session:
        repo = PermissionRepository(session)
        if repo.count():
            return
        for code, module, name_ar, name_en in _DEFAULT_PERMISSIONS:
            repo.add(Permission(code=code, module=module, name_ar=name_ar, name_en=name_en))
    logger.info("Seeded {count} default permissions", count=len(_DEFAULT_PERMISSIONS))


def _install_exception_hook() -> None:
    """Log uncaught exceptions instead of letting Qt silently swallow them.

    PySide6's default behavior for an exception raised inside a Qt slot
    is to print a traceback to stderr and keep running - useful for not
    crashing the whole desktop session over one bad click, but easy to
    miss entirely once the app is packaged and stderr isn't visible.
    """

    def _log_uncaught(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.opt(exception=(exc_type, exc_value, exc_traceback)).critical(
            "Unhandled exception"
        )

    sys.excepthook = _log_uncaught


class ApplicationController:
    """Owns the login <-> main-window lifecycle for one running process.

    A thin state machine with exactly two states: showing the login
    window, or showing the main window for an authenticated session.
    Both windows share the same :class:`~utils.security.SessionManager`
    instance, so the idle-timeout clock main window polls actually
    reflects the session started at login.
    """

    def __init__(self) -> None:
        """Create the controller and show the initial login window."""
        self._session_manager = SessionManager()
        self._main_window: MainWindow | None = None
        self._login_window: LoginWindow | None = None
        self._show_login_window()

    def _show_login_window(self) -> None:
        """Show a fresh login window, closing any existing main window first."""
        if self._main_window is not None:
            self._main_window.close_for_transition()
            self._main_window = None

        self._login_window = LoginWindow(session_manager=self._session_manager)
        self._login_window.login_successful.connect(self._on_login_successful)
        self._login_window.show()

    def _on_login_successful(self, user: dict, company_id: int) -> None:
        """Replace the login window with a fully-wired main window.

        Args:
            user: The authenticated user's data.
            company_id: The company they logged into.
        """
        if self._login_window is not None:
            self._login_window.close()
            self._login_window = None

        window = MainWindow(
            company_id=company_id, current_user=user, session_manager=self._session_manager
        )
        window.logout_requested.connect(self._on_logout)
        window.session_expired.connect(self._on_session_expired)

        window.register_page("dashboard", "لوحة التحكم", DashboardPage(company_id=company_id))
        window.register_page("employees", "الموظفون", EmployeesPage(company_id=company_id))
        window.register_page(
            "attendance", "الحضور والانصراف", AttendancePage(company_id=company_id)
        )
        window.register_page("departments", "الأقسام", DepartmentsPage(company_id=company_id))
        window.register_page("devices", "الأجهزة", DevicesPage(company_id=company_id))
        window.register_page("shifts", "الورديات", ShiftsPage(company_id=company_id))
        window.register_page("holidays", "العطلات", HolidaysPage(company_id=company_id))
        window.register_page("leave", "الإجازات", LeavePage(company_id=company_id))
        window.register_page("reports", "التقارير", ReportsPage(company_id=company_id))
        window.register_page("users", "المستخدمون", UsersPage(company_id=company_id))
        window.register_page("settings", "الإعدادات", SettingsPage(company_id=company_id))
        window.show_page("dashboard")

        self._main_window = window
        window.show()

    def _on_logout(self) -> None:
        """Handle an explicit logout click: end the session and return to login."""
        self._perform_logout()
        self._show_login_window()

    def _on_session_expired(self) -> None:
        """Handle an idle-timeout expiry: notify the user and return to login."""
        self._perform_logout()
        QMessageBox.information(
            None,
            "انتهت الجلسة",
            "انتهت مهلة الجلسة بسبب عدم النشاط. الرجاء تسجيل الدخول مرة أخرى.",
        )
        self._show_login_window()

    def _perform_logout(self) -> None:
        """End the current session, writing the audit trail if one is active."""
        company_id = self._session_manager.current_company_id
        if company_id is None:
            self._session_manager.end_session()
            return

        from controllers.auth_controller import AuthController

        controller = AuthController(company_id=company_id, session_manager=self._session_manager)
        controller.logout()


def main() -> int:
    """Run the desktop application; returns the process exit code."""
    config = get_config()
    config.paths.ensure_created()
    setup_logging()
    _install_exception_hook()
    logger.info(
        "Starting {app_name} v{version} ({environment})",
        app_name=config.app_name,
        version=config.app_version,
        environment=config.environment.value,
    )

    app = QApplication(sys.argv)
    app.setApplicationName(config.app_name)
    app.setApplicationVersion(config.app_version)
    app.setOrganizationName(config.organization_name)
    # This app manages its own window lifecycle explicitly (license window ->
    # login window -> main window, and back again on logout/session-expiry),
    # which always involves closing the old top-level window just before
    # showing its replacement - a transient instant with zero visible
    # windows. Qt's default quitOnLastWindowClosed=True queues an
    # application-quit the moment that happens, regardless of a replacement
    # window being shown microseconds later; every explicit exit path below
    # already calls app.quit() itself, so this heuristic only ever causes
    # harm here (see the regression this fixes: activating a license closed
    # the activation window, and the very next app.processEvents() call
    # picked up that queued quit and ended the process before the freshly
    # constructed, visible LoginWindow ever got a chance to run).
    app.setQuitOnLastWindowClosed(False)

    get_locale_manager().bind_application(app)
    get_theme_manager().bind_application(app)

    # Shared with the nested closures below so a database failure (which can
    # now be discovered either before app.exec() starts, if already
    # licensed, or from inside a Qt slot after it starts, once activation
    # succeeds) has one place to record the real exit code, and so
    # ApplicationController has a reference that outlives _launch_app()'s
    # own local scope for the rest of the process's lifetime.
    run_state: dict[str, object] = {}

    def _launch_app() -> None:
        """Run the rest of startup: database, permissions, splash, main app.

        Gated behind a valid license (see below) - never called until
        :class:`~licensing.license_service.LicenseService` confirms one.
        """
        database = get_database()
        try:
            database.initialize()
        except DatabaseConnectionError as exc:
            logger.critical("Failed to initialize the database: {error}", error=str(exc))
            QMessageBox.critical(
                None, "خطأ في قاعدة البيانات", f"تعذر الاتصال بقاعدة البيانات:\n{exc}"
            )
            run_state["exit_code"] = 1
            app.quit()
            return

        _seed_default_permissions()

        splash = build_splash_screen(app_name=config.app_name_ar)
        splash.show()
        app.processEvents()

        run_state["controller"] = ApplicationController()
        QTimer.singleShot(config.ui.splash_screen_duration_ms, splash.close)

    license_service = LicenseService()
    if license_service.get_status().is_valid:
        QTimer.singleShot(0, _launch_app)
    else:
        license_window = LicenseActivationWindow(license_service=license_service)

        def _on_license_activated() -> None:
            license_window.close()
            _launch_app()

        license_window.activated.connect(_on_license_activated)
        license_window.show()
        run_state["license_window"] = license_window

    exit_code = app.exec()

    get_database().dispose()
    final_code = run_state.get("exit_code", exit_code)
    logger.info("{app_name} exited with code {code}", app_name=config.app_name, code=final_code)
    return final_code


if __name__ == "__main__":
    sys.exit(main())

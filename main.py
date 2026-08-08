"""Composition root: bootstraps logging, the database, and the Qt application.

Run directly (``python main.py``) to start the desktop application.
This module owns the process-wide startup sequence and the
first-run-wizard/login <-> main-window session lifecycle; it
deliberately contains no business logic of its own - every screen is
built in ``ui/`` and every operation goes through ``controllers/``.

This application works fully offline after installation: it never
requires a central Attendance Server, a subscription check, or the
Developer Suite for normal operation. A brand-new installation (no
local company yet) is walked through :class:`~ui.first_run_wizard.FirstRunWizard`
instead of the login screen; every later launch goes straight to
:class:`~ui.login_window.LoginWindow`.
"""

from __future__ import annotations

import sys
from types import TracebackType

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from config import get_config
from database.database import DatabaseConnectionError, get_database, session_scope
from models.permission import Permission
from repositories.permission_repository import PermissionRepository
from services.company_service import CompanyService
from services.scheduler_service import SchedulerService
from ui.attendance import AttendancePage
from ui.branches import BranchesPage
from ui.dashboard_page import DashboardPage
from ui.departments import DepartmentsPage
from ui.devices import DevicesPage
from ui.employees import EmployeesPage
from ui.first_run_wizard import FirstRunWizard
from ui.holidays import HolidaysPage
from ui.leave import LeavePage
from ui.login_window import LoginWindow
from ui.main_window import MainWindow
from ui.payroll import PayrollPage
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
    ("branches.view", "branches", "عرض الفروع", "View Branches"),
    ("branches.manage", "branches", "إدارة الفروع", "Manage Branches"),
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
    ("payroll.view", "payroll", "عرض الرواتب", "View Payroll"),
    (
        "payroll.manage_rules",
        "payroll",
        "إدارة قواعد الرواتب التلقائية",
        "Manage Automatic Payroll Rules",
    ),
    (
        "payroll.manage_adjustments",
        "payroll",
        "إدارة الخصومات والمكافآت",
        "Manage Deductions & Bonuses",
    ),
    ("payroll.finalize", "payroll", "اعتماد الرواتب نهائياً", "Finalize Payroll"),
    ("payroll.reopen", "payroll", "إعادة فتح رواتب معتمدة", "Reopen Finalized Payroll"),
]

# (route, sidebar label, page class, required permission codes) - the
# sidebar/page registry consulted by ApplicationController._on_login_successful.
# A page is only constructed and registered if the signed-in user's role
# grants at least one of its required codes; this is the other half of RBAC
# enforcement (controllers/base_controller.py's requires_permission is the
# per-action half) - a user never sees a sidebar entry for a screen every
# action on it would just reject anyway.
_PAGE_DEFINITIONS: list[tuple[str, str, type, tuple[str, ...]]] = [
    ("dashboard", "لوحة التحكم", DashboardPage, ("dashboard.view",)),
    ("employees", "الموظفون", EmployeesPage, ("employees.view", "employees.manage")),
    (
        "attendance",
        "الحضور والانصراف",
        AttendancePage,
        ("attendance.view", "attendance.manage"),
    ),
    ("departments", "الأقسام", DepartmentsPage, ("departments.view", "departments.manage")),
    ("branches", "الفروع", BranchesPage, ("branches.view", "branches.manage")),
    ("devices", "الأجهزة", DevicesPage, ("devices.view", "devices.manage")),
    ("shifts", "الورديات", ShiftsPage, ("shifts.view", "shifts.manage")),
    ("holidays", "العطلات", HolidaysPage, ("holidays.view", "holidays.manage")),
    ("leave", "الإجازات", LeavePage, ("leave.view", "leave.manage")),
    ("reports", "التقارير", ReportsPage, ("reports.view", "reports.export")),
    (
        "payroll",
        "الرواتب",
        PayrollPage,
        (
            "payroll.view",
            "payroll.manage_rules",
            "payroll.manage_adjustments",
            "payroll.finalize",
            "payroll.reopen",
        ),
    ),
    ("users", "المستخدمون", UsersPage, ("users.view", "users.manage", "roles.manage")),
    (
        "settings",
        "الإعدادات",
        SettingsPage,
        ("settings.view", "settings.manage", "backup.manage"),
    ),
]


def _seed_default_permissions() -> None:
    """Seed the global permission catalog, adding any codes it is missing.

    Runs on every startup, not just the first: an existing, already-
    initialized database (a real customer install) never gets a fresh
    catalog insert, so a later release that adds new permission codes
    (e.g. payroll) would otherwise never reach it. Checking per-code
    instead of "catalog is empty" makes this a true migration path --
    existing codes and the roles that already grant them are untouched,
    only genuinely missing codes are added.
    """
    added = 0
    with session_scope() as session:
        repo = PermissionRepository(session)
        for code, module, name_ar, name_en in _DEFAULT_PERMISSIONS:
            if repo.get_by_code(code) is not None:
                continue
            repo.add(Permission(code=code, module=module, name_ar=name_ar, name_en=name_en))
            added += 1
    if added:
        logger.info("Seeded {count} new default permission(s)", count=added)


def _has_any_company() -> bool:
    """Whether this installation has ever completed the first-run wizard."""
    with session_scope() as session:
        return bool(CompanyService(session).list_companies())


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
    """Owns the first-run-wizard/login <-> main-window lifecycle for one running process.

    A thin state machine: showing the first-run wizard (only for an
    installation with no company yet), showing the login window, or
    showing the main window for an authenticated session. Every window
    shares the same :class:`~utils.security.SessionManager` instance,
    so the idle-timeout clock the main window polls actually reflects
    the session started at login.
    """

    def __init__(self) -> None:
        """Create the controller and show the first-run wizard or login window."""
        self._session_manager = SessionManager()
        self._main_window: MainWindow | None = None
        self._login_window: LoginWindow | None = None
        self._wizard: FirstRunWizard | None = None
        if _has_any_company():
            self._show_login_window()
        else:
            self._show_first_run_wizard()

    def _show_first_run_wizard(self) -> None:
        """Show the first-run setup wizard for a brand-new installation."""
        self._wizard = FirstRunWizard()
        self._wizard.setup_completed.connect(self._on_login_successful)
        self._wizard.show()

    def _show_login_window(self) -> None:
        """Show a fresh login window, closing any existing main window first."""
        if self._main_window is not None:
            self._main_window.close_for_transition()
            self._main_window = None

        self._login_window = LoginWindow(session_manager=self._session_manager)
        self._login_window.login_successful.connect(self._on_login_successful)
        self._login_window.show()

    def _on_login_successful(self, user: dict, company_id: int) -> None:
        """Replace the wizard/login window with a fully-wired main window.

        Args:
            user: The authenticated user's data.
            company_id: The company they logged into.
        """
        if self._wizard is not None:
            self._wizard.close()
            self._wizard = None
        if self._login_window is not None:
            self._login_window.close()
            self._login_window = None

        window = MainWindow(
            company_id=company_id, current_user=user, session_manager=self._session_manager
        )
        window.logout_requested.connect(self._on_logout)
        window.session_expired.connect(self._on_session_expired)

        permission_codes = frozenset(user.get("permission_codes") or [])
        for route, label, page_cls, required_codes in _PAGE_DEFINITIONS:
            if not set(required_codes) & permission_codes:
                continue
            page = page_cls(
                company_id=company_id,
                current_user_id=user["id"],
                permission_codes=permission_codes,
            )
            window.register_page(route, label, page)

        if window.page_stack.count() == 0:
            # A role with literally no granted permissions (e.g. a custom
            # role an admin created but never assigned any code to) - not
            # something the seeded built-in roles can produce, but a real
            # possibility for a hand-built one. Leave the user logged in
            # (they can still reach the top bar's logout) rather than
            # crashing on an empty page stack.
            QMessageBox.warning(
                window,
                "لا توجد صلاحيات",
                "حساب المستخدم الحالي لا يملك أي صلاحية للوصول إلى شاشات النظام. "
                "الرجاء التواصل مع مسؤول النظام.",
            )

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
    # This app manages its own window lifecycle explicitly (first-run wizard
    # / login window -> main window, and back again on logout/session
    # -expiry), which always involves closing the old top-level window just
    # before showing its replacement - a transient instant with zero
    # visible windows. Qt's default quitOnLastWindowClosed=True queues an
    # application-quit the moment that happens, regardless of a replacement
    # window being shown microseconds later; every explicit exit path below
    # already calls app.quit() itself, so this heuristic only ever causes
    # harm here (see the regression this originally fixed: closing a gating
    # window closed it, and the very next app.processEvents() call picked
    # up that queued quit and ended the process before the freshly
    # constructed, visible replacement window ever got a chance to run).
    app.setQuitOnLastWindowClosed(False)

    get_locale_manager().bind_application(app)
    get_theme_manager().bind_application(app)

    # Shared with the nested closure below so a database failure (which can
    # only ever be discovered before app.exec() starts, since startup never
    # blocks on anything external anymore) has one place to record the real
    # exit code, and so ApplicationController has a reference that outlives
    # _launch_app()'s own local scope for the rest of the process's
    # lifetime.
    run_state: dict[str, object] = {}
    scheduler = SchedulerService()

    def _launch_app() -> None:
        """Run the rest of startup: database, permissions, splash, first screen."""
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
        scheduler.start()

        splash = build_splash_screen(app_name=config.app_name_ar)
        splash.show()
        app.processEvents()

        run_state["controller"] = ApplicationController()
        QTimer.singleShot(config.ui.splash_screen_duration_ms, splash.close)

    QTimer.singleShot(0, _launch_app)

    exit_code = app.exec()

    scheduler.shutdown()
    get_database().dispose()
    final_code = run_state.get("exit_code", exit_code)
    logger.info("{app_name} exited with code {code}", app_name=config.app_name, code=final_code)
    return final_code


if __name__ == "__main__":
    sys.exit(main())

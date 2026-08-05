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
from models.permission import Permission
from models.update_state import ClientUpdateStatus
from repositories.company_settings_repository import CompanySettingsRepository
from repositories.permission_repository import PermissionRepository
from repositories.update_state_repository import ClientUpdateStateRepository
from services.scheduler_service import SchedulerService
from services.subscription_check_service import SubscriptionCheckService
from sync.coordinator import ClientSyncCoordinator
from sync.scheduler import ClientSyncSchedulerService
from updates.checker import UpdateCheckService
from updates.keys import load_public_key
from ui.attendance import AttendancePage
from ui.branches import BranchesPage
from ui.dashboard_page import DashboardPage
from ui.departments import DepartmentsPage
from ui.devices import DevicesPage
from ui.employees import EmployeesPage
from ui.holidays import HolidaysPage
from ui.leave import LeavePage
from ui.login_window import LoginWindow
from ui.main_window import MainWindow
from ui.reports import ReportsPage
from ui.settings import SettingsPage
from ui.shifts import ShiftsPage
from ui.subscription_blocked_window import SubscriptionBlockedWindow
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
    ("users", "المستخدمون", UsersPage, ("users.view", "users.manage", "roles.manage")),
    (
        "settings",
        "الإعدادات",
        SettingsPage,
        ("settings.view", "settings.manage", "backup.manage"),
    ),
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


def _show_remote_configuration_restart_notice(parent, company_id: int) -> None:
    """Show and clear a pending "restart required" notice for this company, if any.

    Checked once per login (rather than at raw startup) so it is
    always shown against the company the user actually just logged
    into, matching :mod:`sync.configuration_apply`'s own "first active
    company" application scoping. A no-op for the common case where no
    remote configuration was ever applied, or the last one applied
    needed no restart.
    """
    with session_scope() as session:
        settings = CompanySettingsRepository(session, company_id=company_id).get_for_company()
        if settings is None or not settings.remote_config_restart_required:
            return
        settings.remote_config_restart_required = False

    QMessageBox.information(
        parent,
        "مطلوب إعادة التشغيل",
        "تم تطبيق إعدادات جديدة من الخادم. يرجى إعادة تشغيل التطبيق لإتمام التفعيل الكامل.",
    )


def _show_update_notice_if_needed(parent) -> None:
    """Show a software-update notice once per login, if one is pending.

    Only ever informs or offers a postponement - this function never
    installs anything itself (Phase 14 covers download and
    verification, not unattended installation). A mandatory update
    gets an information-only dialog with no postpone option, matching
    :meth:`updates.checker.UpdateCheckService.is_postponable`; every
    other update type gets a Yes/No dialog that postpones for 24 hours
    on "No".
    """
    with session_scope() as session:
        state = ClientUpdateStateRepository(session).get_latest()
        if state is None or state.status not in (
            ClientUpdateStatus.DISCOVERED.value,
            ClientUpdateStatus.VERIFIED.value,
        ):
            return
        update_version_id = state.update_version_id
        version = state.version
        update_type = state.update_type
        release_notes = state.release_notes or ""
        is_verified = state.status == ClientUpdateStatus.VERIFIED.value

    ready_note = (
        "تم تنزيل التحديث والتحقق منه وهو جاهز للتثبيت."
        if is_verified
        else "سيتم تنزيل التحديث في الخلفية."
    )

    if update_type == "mandatory":
        QMessageBox.information(
            parent,
            "تحديث إلزامي متوفر",
            f"يتوجد تحديث إلزامي إلى الإصدار {version}.\n\n{release_notes}\n\n{ready_note}\n"
            "يجب تثبيت هذا التحديث لمتابعة استخدام النظام.",
        )
        return

    answer = QMessageBox.question(
        parent,
        "تحديث متوفر",
        f"يتوجد تحديث جديد إلى الإصدار {version}.\n\n{release_notes}\n\n{ready_note}\n"
        "هل ترغب بتأجيل هذا التحديث ليوم واحد؟",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if answer == QMessageBox.Yes:
        from datetime import datetime, timedelta, timezone

        checker = UpdateCheckService(
            get_database(),
            get_config().sync.server_url,
            current_version=get_config().app_version,
            package_type=get_config().updates.package_type,
            downloads_dir=get_config().updates.downloads_dir,
            public_key=load_public_key(),
        )
        try:
            checker.postpone(update_version_id, until=datetime.now(timezone.utc) + timedelta(hours=24))
        except Exception:  # noqa: BLE001 - postponing is a courtesy, never a hard requirement
            pass


class ApplicationController:
    """Owns the login <-> main-window lifecycle for one running process.

    A thin state machine with three states: showing the login window,
    showing the post-login subscription-blocked screen, or showing the
    main window for an authenticated session. Every window shares the
    same :class:`~utils.security.SessionManager` instance, so the
    idle-timeout clock the main window polls actually reflects the
    session started at login.
    """

    def __init__(self, subscription_check_service: SubscriptionCheckService) -> None:
        """Create the controller and show the initial login window.

        Args:
            subscription_check_service: Performs this device's
                subscription/enrollment check right after each
                successful local login (see
                :meth:`_on_login_successful`) — this application has
                no preconfigured company, so this is the only place
                that check ever runs.
        """
        self._session_manager = SessionManager()
        self._subscription_check_service = subscription_check_service
        self._main_window: MainWindow | None = None
        self._login_window: LoginWindow | None = None
        self._blocked_window: SubscriptionBlockedWindow | None = None
        self._pending_login: tuple[dict, int] | None = None
        self._show_login_window()

    def _show_login_window(self) -> None:
        """Show a fresh login window, closing any existing main window first."""
        if self._main_window is not None:
            self._main_window.close_for_transition()
            self._main_window = None

        self._login_window = LoginWindow(
            subscription_check_service=self._subscription_check_service,
            session_manager=self._session_manager,
        )
        self._login_window.login_successful.connect(self._on_login_successful)
        self._login_window.show()

    def _on_login_successful(self, user: dict, company_id: int) -> None:
        """Verify this device's live subscription status, then proceed or block.

        A fresh device's company/subscription enrollment already
        happened inside :class:`~ui.login_window.LoginWindow` itself
        (before local authentication even ran — see
        :meth:`~services.subscription_check_service.SubscriptionCheckService.resolve_company_code`),
        so this is always a plain, already-enrolled status check
        (:meth:`~services.subscription_check_service.SubscriptionCheckService.check`)
        — the last thing standing between a successful local login and
        the main window.

        Args:
            user: The authenticated user's data.
            company_id: The company they logged into.
        """
        result = self._subscription_check_service.check()
        if not result.allowed:
            self._pending_login = (user, company_id)
            self._perform_logout()
            if self._login_window is not None:
                self._login_window.close()
                self._login_window = None
            self._show_blocked_window(result.message_ar)
            return

        self._enter_main_window(user, company_id)

    def _show_blocked_window(self, message_ar: str) -> None:
        """Show the subscription-blocked screen for the login just denied."""
        blocked = SubscriptionBlockedWindow(recheck=self._subscription_check_service.check)
        blocked.show_result(message_ar)
        blocked.passed.connect(self._on_blocked_passed)
        blocked.dismissed.connect(self._on_blocked_dismissed)
        self._blocked_window = blocked
        blocked.show()

    def _on_blocked_passed(self) -> None:
        """A retry succeeded: close the blocked screen and proceed into the app."""
        self._blocked_window.close()
        self._blocked_window = None
        user, company_id = self._pending_login
        self._pending_login = None
        self._enter_main_window(user, company_id)

    def _on_blocked_dismissed(self) -> None:
        """The blocked screen was closed without a retry succeeding: return to login."""
        self._blocked_window = None
        self._pending_login = None
        self._show_login_window()

    def _enter_main_window(self, user: dict, company_id: int) -> None:
        """Replace the login/blocked window with a fully-wired main window.

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
        _show_remote_configuration_restart_notice(window, company_id)
        _show_update_notice_if_needed(window)

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
    # This app manages its own window lifecycle explicitly (subscription
    # -blocked window -> login window -> main window, and back again on
    # logout/session-expiry), which always involves closing the old
    # top-level window just before showing its replacement - a transient
    # instant with zero visible windows. Qt's default
    # quitOnLastWindowClosed=True queues an application-quit the moment that
    # happens, regardless of a replacement window being shown microseconds
    # later; every explicit exit path below already calls app.quit() itself,
    # so this heuristic only ever causes harm here (see the regression this
    # originally fixed: closing a gating window closed it, and the very next
    # app.processEvents() call picked up that queued quit and ended the
    # process before the freshly constructed, visible replacement window
    # ever got a chance to run).
    app.setQuitOnLastWindowClosed(False)

    get_locale_manager().bind_application(app)
    get_theme_manager().bind_application(app)

    # Shared with the nested closures below so a database failure (which can
    # now be discovered either before app.exec() starts, if the subscription
    # is already valid, or from inside a Qt slot after it starts, once a
    # retry passes) has one place to record the real exit code, and so
    # ApplicationController has a reference that outlives _launch_app()'s
    # own local scope for the rest of the process's lifetime.
    run_state: dict[str, object] = {}
    scheduler = SchedulerService()
    sync_scheduler_holder: dict[str, ClientSyncSchedulerService] = {}

    def _launch_app() -> None:
        """Run the rest of startup: database, background sync, splash, login screen.

        No subscription/enrollment check happens here — a fresh
        installation has no company to check yet in this application's
        multi-tenant, central-server deployment; that only gets
        established once a user actually logs in (see
        :class:`ApplicationController`).
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
        scheduler.start()

        sync_coordinator = ClientSyncCoordinator(database, config.sync.server_url)
        subscription_check_service = SubscriptionCheckService(
            database, sync_coordinator, device_name=config.sync.device_name
        )

        # Safe to start before this device has enrolled -- both the sync
        # cycle and (transitively) the update check are no-ops until
        # ClientSyncCoordinator.is_enrolled() is True (see
        # ClientSyncSchedulerService._run_cycle), which now only happens
        # once a user's first login establishes this device's company.
        update_checker = (
            UpdateCheckService(
                database,
                config.sync.server_url,
                current_version=config.app_version,
                package_type=config.updates.package_type,
                downloads_dir=config.updates.downloads_dir,
                public_key=load_public_key(),
            )
            if config.updates.enabled
            else None
        )
        sync_scheduler = ClientSyncSchedulerService(
            sync_coordinator,
            database,
            sync_enabled=config.sync.enabled,
            sync_interval_seconds=config.sync.interval_seconds,
            update_check_service=update_checker,
        )
        sync_scheduler.start()
        sync_scheduler_holder["scheduler"] = sync_scheduler

        splash = build_splash_screen(app_name=config.app_name_ar)
        splash.show()
        app.processEvents()

        run_state["controller"] = ApplicationController(subscription_check_service)
        QTimer.singleShot(config.ui.splash_screen_duration_ms, splash.close)

    QTimer.singleShot(0, _launch_app)

    exit_code = app.exec()

    scheduler.shutdown()
    if "scheduler" in sync_scheduler_holder:
        sync_scheduler_holder["scheduler"].shutdown()
    get_database().dispose()
    final_code = run_state.get("exit_code", exit_code)
    logger.info("{app_name} exited with code {code}", app_name=config.app_name, code=final_code)
    return final_code


if __name__ == "__main__":
    sys.exit(main())

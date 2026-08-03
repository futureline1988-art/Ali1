"""Developer Suite composition root.

Mirrors the Attendance Client's own ``main.py`` shape (load config,
initialize the database, build the Qt application and main window) —
deliberately the same pattern, applied to this application's own,
independent configuration and database.

Phase 11 adds a login gate in front of :class:`~developer_suite.ui.main_window.MainWindow`:
a silent :meth:`~developer_suite.admin.session_manager.AdminSessionManager.try_auto_login`
attempt first (a "remembered" session from a previous run), falling
back to :class:`~developer_suite.ui.login_window.LoginWindow` if that
fails. The main window is never constructed until one of the two
succeeds — mirroring exactly how the Attendance Client's own
``main.py`` gates ``ui.main_window.MainWindow`` behind
``ui.login_window.LoginWindow``.

Run directly for local development::

    python -m developer_suite.main
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from developer_suite.config import DeveloperSuiteConfig, get_developer_suite_config
from developer_suite.container import ServiceContainer
from developer_suite.database.bootstrap import build_database
from developer_suite.ui.login_window import LoginWindow
from developer_suite.ui.main_window import MainWindow


def main() -> int:
    """Run the Developer Suite desktop application; returns the process exit code."""
    config: DeveloperSuiteConfig = get_developer_suite_config()
    database = build_database(config)

    app = QApplication(sys.argv)
    app.setApplicationName(config.app_name)
    app.setApplicationVersion(config.app_version)

    container = ServiceContainer(config=config, database=database)
    container.sync_scheduler.start()

    windows: dict[str, object] = {}

    def _show_main_window() -> None:
        window = MainWindow(container)
        window.show()
        windows["main"] = window  # keep a reference alive past this function's return

    if container.admin_session_manager.try_auto_login():
        _show_main_window()
    else:
        login_window = LoginWindow(container.admin_session_manager)

        def _on_login_successful() -> None:
            login_window.close()
            _show_main_window()

        login_window.login_successful.connect(_on_login_successful)
        login_window.show()
        windows["login"] = login_window

    exit_code = app.exec()
    container.sync_scheduler.shutdown()
    database.dispose()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

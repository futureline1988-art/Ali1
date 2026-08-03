"""Developer Suite composition root.

Mirrors the Attendance Client's own ``main.py`` shape (load config,
initialize the database, build the Qt application and main window) —
deliberately the same pattern, applied to this application's own,
independent configuration and database.

Run directly for local development::

    python -m developer_suite.main
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from developer_suite.config import DeveloperSuiteConfig, get_developer_suite_config
from developer_suite.container import ServiceContainer
from developer_suite.database.bootstrap import build_database
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
    window = MainWindow(container)
    window.show()

    exit_code = app.exec()
    container.sync_scheduler.shutdown()
    database.dispose()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

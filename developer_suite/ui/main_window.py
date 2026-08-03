"""The Developer Suite's main window: navigation sidebar + page stack + status bar.

Depends only on :class:`~developer_suite.container.ServiceContainer`
and the :class:`~developer_suite.modules.base.PlatformModule` interface
— it has no knowledge of what any module actually does, so a later
phase giving a module real business logic never requires a change
here. The one duck-typed exception (Phase 12): if a built page exposes
a Qt ``navigate_requested`` signal (see
:attr:`~developer_suite.ui.dashboard_page.DashboardPage.navigate_requested`),
this window connects it to :meth:`MainWindow.show_module` — the same
"any module's page may opt into this" convention every module's
:meth:`~developer_suite.modules.base.PlatformModule.build_page` already
follows, not a dependency on any specific module.

Phase 12's status bar reuses
:class:`~developer_suite.container.ServiceContainer`'s existing
:attr:`~developer_suite.container.ServiceContainer.dashboard_refresh_service`
for its Attendance Server/Database/Synchronization fields — the exact
same background-refreshed snapshot the Dashboard page itself renders,
so the status bar never issues a second, independent set of network
requests. Current Administrator/Current Version are read once at
construction, since neither changes for the lifetime of a signed-in
window.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QStackedWidget, QWidget

from developer_suite.container import ServiceContainer
from developer_suite.services.dashboard_service import DashboardSnapshot
from developer_suite.ui.navigation import NavigationSidebar

_STATUS_BAR_SEPARATOR = "  |  "


def _server_status_text(snapshot: DashboardSnapshot) -> str:
    if snapshot.server_reachable is None:
        return "خادم الحضور: غير معروف"
    if not snapshot.server_reachable:
        return "خادم الحضور: غير متصل"
    return f"خادم الحضور: متصل (إصدار {snapshot.server_version or '؟'})"


def _database_status_text(snapshot: DashboardSnapshot) -> str:
    if snapshot.database_connected is None:
        return "قاعدة البيانات: غير معروفة"
    return "قاعدة البيانات: متصلة" if snapshot.database_connected else "قاعدة البيانات: غير متصلة"


def _synchronization_status_text(snapshot: DashboardSnapshot) -> str:
    if snapshot.pending_sync_count > 0:
        return f"المزامنة: {snapshot.pending_sync_count} بانتظار الإرسال"
    if snapshot.last_sync_at is not None:
        return f"المزامنة: محدثة (آخر مزامنة {snapshot.last_sync_at.strftime('%Y-%m-%d %H:%M')})"
    return "المزامنة: لم تتم بعد"


class MainWindow(QMainWindow):
    """The Developer Suite's top-level window."""

    def __init__(self, container: ServiceContainer, *, parent: QWidget | None = None) -> None:
        """Build the window shell (sidebar + page stack + status bar).

        Args:
            container: Provides every registered platform module and
                the shared background refresh service.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._container = container

        self.setWindowTitle(container.config.app_name)
        self.setMinimumSize(1024, 640)

        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        modules = container.modules()
        entries = [(module.module_id, module.display_name_ar) for module in modules]

        self.sidebar = NavigationSidebar(entries, parent=central)
        self.sidebar.setFixedWidth(240)
        self.sidebar.module_selected.connect(self.show_module)
        root_layout.addWidget(self.sidebar)

        self.page_stack = QStackedWidget(central)
        root_layout.addWidget(self.page_stack)

        self._page_index_by_module_id: dict[str, int] = {}
        for module in modules:
            page = module.build_page()
            navigate_requested = getattr(page, "navigate_requested", None)
            if navigate_requested is not None:
                navigate_requested.connect(self.show_module)
            index = self.page_stack.addWidget(page)
            self._page_index_by_module_id[module.module_id] = index

        if modules:
            self.show_module(modules[0].module_id)

        self._build_status_bar(container)
        container.dashboard_refresh_service.snapshot_ready.connect(self._on_snapshot_ready)

    def _build_status_bar(self, container: ServiceContainer) -> None:
        """Build the persistent status bar and populate its static fields.

        Args:
            container: Supplies :attr:`~developer_suite.container.ServiceContainer.admin_session_manager`
                and :attr:`~developer_suite.container.ServiceContainer.config`
                for the two fields that never change during a session.
        """
        status_bar = self.statusBar()

        self.server_status_label = QLabel("خادم الحضور: ...", status_bar)
        status_bar.addWidget(self.server_status_label)

        self.database_status_label = QLabel(_STATUS_BAR_SEPARATOR + "قاعدة البيانات: ...", status_bar)
        status_bar.addWidget(self.database_status_label)

        self.synchronization_status_label = QLabel(_STATUS_BAR_SEPARATOR + "المزامنة: ...", status_bar)
        status_bar.addWidget(self.synchronization_status_label)

        account = container.admin_session_manager.current_account
        administrator_text = f"المسؤول: {account.username}" if account is not None else "المسؤول: —"
        self.administrator_label = QLabel(administrator_text, status_bar)
        status_bar.addPermanentWidget(self.administrator_label)

        version_label = QLabel(f"الإصدار {container.config.app_version}", status_bar)
        status_bar.addPermanentWidget(version_label)

    def _on_snapshot_ready(self, snapshot: DashboardSnapshot) -> None:
        """Refresh the status bar's live fields from a newly computed snapshot."""
        self.server_status_label.setText(_server_status_text(snapshot))
        self.database_status_label.setText(_STATUS_BAR_SEPARATOR + _database_status_text(snapshot))
        self.synchronization_status_label.setText(
            _STATUS_BAR_SEPARATOR + _synchronization_status_text(snapshot)
        )

    def show_module(self, module_id: str) -> None:
        """Switch the content area to the given module's page.

        Args:
            module_id: A registered
                :attr:`~developer_suite.modules.base.PlatformModule.module_id`.

        Raises:
            KeyError: No module with that id is registered.
        """
        index = self._page_index_by_module_id[module_id]
        self.page_stack.setCurrentIndex(index)

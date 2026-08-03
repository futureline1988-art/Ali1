"""The Developer Suite's main window: navigation sidebar + page stack.

Depends only on :class:`~developer_suite.container.ServiceContainer`
and the :class:`~developer_suite.modules.base.PlatformModule` interface
— it has no knowledge of what any module actually does, so a later
phase giving a module real business logic never requires a change
here.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QStackedWidget, QWidget

from developer_suite.container import ServiceContainer
from developer_suite.ui.navigation import NavigationSidebar


class MainWindow(QMainWindow):
    """The Developer Suite's top-level window."""

    def __init__(self, container: ServiceContainer, *, parent: QWidget | None = None) -> None:
        """Build the window shell (sidebar + empty-until-selected page stack).

        Args:
            container: Provides every registered platform module.
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
            index = self.page_stack.addWidget(page)
            self._page_index_by_module_id[module.module_id] = index

        if modules:
            self.show_module(modules[0].module_id)

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

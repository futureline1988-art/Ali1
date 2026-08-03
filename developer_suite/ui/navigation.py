"""The Developer Suite's navigation sidebar.

A small, self-contained widget (see this package's ``__init__.py`` for
why it is not shared with the Attendance Client's own sidebar): a list
of checkable buttons, one per registered
:class:`~developer_suite.modules.base.PlatformModule`, emitting a
signal on selection. Has no knowledge of the content area it controls
— :class:`~developer_suite.ui.main_window.MainWindow` owns switching
the displayed page.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QPushButton, QVBoxLayout, QWidget


class NavigationSidebar(QWidget):
    """Vertical list of navigation entries, one per platform module.

    Attributes:
        module_selected: Emitted with a
            :attr:`~developer_suite.modules.base.PlatformModule.module_id`
            whenever the user selects a different entry.
    """

    module_selected = Signal(str)

    def __init__(
        self,
        entries: list[tuple[str, str]],
        *,
        parent: QWidget | None = None,
    ) -> None:
        """Create a sidebar with the given navigation entries.

        Args:
            entries: ``(module_id, display_name_ar)`` pairs, in display
                order.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setObjectName("DeveloperSuiteNavigationSidebar")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 16, 8, 16)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)

        for index, (module_id, display_name_ar) in enumerate(entries):
            button = QPushButton(display_name_ar)
            button.setObjectName("DeveloperSuiteNavButton")
            button.setCheckable(True)
            button.setMinimumHeight(44)
            button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            button.clicked.connect(lambda _checked, mid=module_id: self.module_selected.emit(mid))
            self._button_group.addButton(button)
            layout.addWidget(button)

            if index == 0:
                button.setChecked(True)

"""The Developer Suite's navigation sidebar.

A small, self-contained widget (see this package's ``__init__.py`` for
why it is not shared with the Attendance Client's own sidebar): a list
of checkable buttons, one per registered
:class:`~developer_suite.modules.base.PlatformModule`, emitting a
signal on selection. Has no knowledge of the content area it controls
— :class:`~developer_suite.ui.main_window.MainWindow` owns switching
the displayed page.

Phase 12 groups the entries visually into "platform" (Dashboard,
Customers, Subscriptions, Remote Configuration, Monitoring, Updates,
Reporting — Phase 15's own module joins this group unmodified) and
"administration" (Server, Settings) with a thin divider — the same
:data:`~developer_suite.modules.ALL_MODULES` order, styled rather than
restructured: still one flat, single-level list, since every "group"
in Phase 12's request maps to exactly one navigation destination, not
a submenu.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

#: module_ids that start the "administration" group, styled with a
#: divider above the first of them — everything before is the
#: "platform" group. Kept as a set of stable ids (never display
#: labels), so this has nothing to translate or keep in sync with
#: wording changes.
_ADMINISTRATION_GROUP_MODULE_IDS = frozenset({"server_status", "settings"})

_SIDEBAR_STYLESHEET = """
QWidget#DeveloperSuiteNavigationSidebar {
    background-color: #1F2937;
}
QLabel#DeveloperSuiteNavHeading {
    color: #F9FAFB;
    font-size: 13pt;
    font-weight: bold;
    padding: 4px 12px 12px 12px;
}
QPushButton#DeveloperSuiteNavButton {
    color: #D1D5DB;
    background-color: transparent;
    border: none;
    border-radius: 6px;
    padding: 0 14px;
    text-align: right;
    font-size: 10.5pt;
}
QPushButton#DeveloperSuiteNavButton:hover {
    background-color: #374151;
    color: #F9FAFB;
}
QPushButton#DeveloperSuiteNavButton:checked {
    background-color: #2563EB;
    color: #FFFFFF;
    font-weight: bold;
}
QFrame#DeveloperSuiteNavDivider {
    background-color: #374151;
    max-height: 1px;
    margin: 8px 12px;
}
"""


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
        self.setStyleSheet(_SIDEBAR_STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 16, 0, 16)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        heading = QLabel("مجموعة أدوات المطورين", self)
        heading.setObjectName("DeveloperSuiteNavHeading")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)

        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)

        for index, (module_id, display_name_ar) in enumerate(entries):
            if module_id in _ADMINISTRATION_GROUP_MODULE_IDS and (
                index == 0 or entries[index - 1][0] not in _ADMINISTRATION_GROUP_MODULE_IDS
            ):
                divider = QFrame(self)
                divider.setObjectName("DeveloperSuiteNavDivider")
                divider.setFrameShape(QFrame.Shape.HLine)
                layout.addWidget(divider)

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

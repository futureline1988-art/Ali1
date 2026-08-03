"""Generic list-and-CRUD-buttons panel, reused by every Remote Configuration tab.

Not part of this package's public interface (leading underscore). Six
near-identical tables-with-add/edit/delete-buttons would otherwise
exist across
:mod:`developer_suite.ui.configuration_editor_page` — one per profile
type plus the bundle list — so the table/toolbar wiring is written
once here and each tab supplies only what differs: its column labels,
how to turn one entity into a row of text, and what add/edit/delete
actually do. Knows nothing about
:mod:`developer_suite.services.configuration_service` or any specific
model — purely a presentation-layer building block.
"""

from __future__ import annotations

from typing import Callable, Generic, TypeVar

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

ItemT = TypeVar("ItemT")


class ProfileListPanel(QWidget, Generic[ItemT]):
    """A titled table of entities with add/edit/delete buttons above it.

    Construct with the callbacks that make this panel concrete for one
    entity type; the panel itself only ever calls them and repopulates
    its table from :meth:`reload`.
    """

    def __init__(
        self,
        *,
        column_labels: tuple[str, ...],
        row_values: Callable[[ItemT], tuple[str, ...]],
        list_items: Callable[[], list[ItemT]],
        on_add: Callable[[], bool],
        on_edit: Callable[[ItemT], bool],
        on_delete: Callable[[ItemT], bool],
        add_label: str,
        edit_label: str,
        delete_label: str,
        delete_confirm_text: Callable[[ItemT], str],
        parent: QWidget | None = None,
    ) -> None:
        """Build the panel and load its initial contents.

        Args:
            column_labels: Table header labels, in column order.
            row_values: Turns one entity into its row's cell text,
                matching ``column_labels`` in length and order.
            list_items: Fetches the current, full list of entities to
                display.
            on_add: Runs the "add" flow (e.g. opening a dialog and
                calling a service). Returns whether the table should
                reload.
            on_edit: Runs the "edit" flow for the selected entity.
                Returns whether the table should reload.
            on_delete: Runs the "delete" flow for the selected entity,
                called only after the user confirms. Returns whether
                the table should reload.
            add_label: Button text for the add action.
            edit_label: Button text for the edit action.
            delete_label: Button text for the delete action.
            delete_confirm_text: Builds the confirmation dialog's
                message for one entity.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._row_values = row_values
        self._list_items = list_items
        self._on_add = on_add
        self._on_edit = on_edit
        self._on_delete = on_delete
        self._delete_confirm_text = delete_confirm_text
        self._items: list[ItemT] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        toolbar.addStretch(1)

        self.add_button = QPushButton(add_label, self)
        self.add_button.clicked.connect(self._on_add_clicked)
        toolbar.addWidget(self.add_button)

        self.edit_button = QPushButton(edit_label, self)
        self.edit_button.clicked.connect(self._on_edit_clicked)
        toolbar.addWidget(self.edit_button)

        self.delete_button = QPushButton(delete_label, self)
        self.delete_button.clicked.connect(self._on_delete_clicked)
        toolbar.addWidget(self.delete_button)

        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(column_labels), self)
        self.table.setHorizontalHeaderLabels(column_labels)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        self.reload()

    def reload(self) -> None:
        """Reload the table from :meth:`list_items`."""
        self._items = self._list_items()
        self.table.setRowCount(len(self._items))
        for row, item in enumerate(self._items):
            for column, text in enumerate(self._row_values(item)):
                self.table.setItem(row, column, QTableWidgetItem(text))

    def selected_item(self) -> ItemT | None:
        """The entity backing the currently selected row, if any."""
        row = self.table.currentRow()
        if row < 0 or row >= len(self._items):
            return None
        return self._items[row]

    def _on_add_clicked(self) -> None:
        if self._on_add():
            self.reload()

    def _on_edit_clicked(self) -> None:
        item = self.selected_item()
        if item is None:
            QMessageBox.information(self, "تعديل", "الرجاء اختيار عنصر أولاً.")
            return
        if self._on_edit(item):
            self.reload()

    def _on_delete_clicked(self) -> None:
        item = self.selected_item()
        if item is None:
            QMessageBox.information(self, "حذف", "الرجاء اختيار عنصر أولاً.")
            return
        confirmed = QMessageBox.question(
            self,
            "تأكيد الحذف",
            self._delete_confirm_text(item),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        if self._on_delete(item):
            self.reload()

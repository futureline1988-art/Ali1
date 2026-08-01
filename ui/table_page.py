"""Shared list/table page scaffold used by every CRUD-style feature screen.

Every "manage X" screen (employees, departments, devices, users) needs
the same skeleton: a heading, a debounced search box, an "add" button,
an extra-actions toolbar slot, a read-only data table, and an inline
error banner. :class:`TablePage` owns exactly that skeleton and nothing
domain-specific — subclasses (in ``ui/employees.py`` etc.) decide what
columns to show, how to load rows, and what "add"/"edit"/"delete" mean.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.widgets import SearchBox, make_heading_label, make_primary_button, make_status_label


class TablePage(QWidget):
    """A reusable "heading + search + toolbar + table" scaffold.

    Attributes:
        table: The underlying read-only, single-row-selection table.
        toolbar_layout: An ``QHBoxLayout`` subclasses may append extra
            buttons to (e.g. a device screen's "test connection"),
            positioned between the search box and the add button.
    """

    add_requested = Signal()
    """Emitted when the add button is clicked (absent if ``add_button_text`` was ``None``)."""

    search_changed = Signal(str)
    """Emitted with the current search text after the debounce interval."""

    row_activated = Signal(dict)
    """Emitted with a row's backing dict when it is double-clicked (typically "edit")."""

    def __init__(
        self,
        *,
        title: str,
        add_button_text: str | None = None,
        search_placeholder: str = "بحث...",
        parent: QWidget | None = None,
    ) -> None:
        """Build the page skeleton.

        Args:
            title: The heading shown at the top of the page.
            add_button_text: Label for a primary "add" button; omit to
                not show one (e.g. a read-only or single-row screen).
            search_placeholder: Placeholder text for the search box.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._rows: list[dict[str, Any]] = []
        self._columns: list[tuple[str, Callable[[dict[str, Any]], str]]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        header_row = QHBoxLayout()
        header_row.addWidget(make_heading_label(title))
        header_row.addStretch(1)

        self.search_box = SearchBox(search_placeholder, parent=self)
        self.search_box.setFixedWidth(280)
        self.search_box.search_changed.connect(self.search_changed.emit)
        header_row.addWidget(self.search_box)

        self.toolbar_layout = QHBoxLayout()
        header_row.addLayout(self.toolbar_layout)

        self.add_button = None
        if add_button_text is not None:
            self.add_button = make_primary_button(add_button_text, parent=self)
            self.add_button.clicked.connect(self.add_requested.emit)
            header_row.addWidget(self.add_button)

        layout.addLayout(header_row)

        self.error_label = make_status_label("", "danger")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.table = QTableWidget(self)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        layout.addWidget(self.table, stretch=1)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_columns(
        self, columns: list[tuple[str, str, Callable[[dict[str, Any]], str]]]
    ) -> None:
        """Define the table's columns.

        Args:
            columns: ``(key, header_label, formatter)`` triples, in
                display order. ``key`` is only used for
                :meth:`row_value` lookups; ``formatter`` converts a row
                dict into that column's display text.
        """
        self._columns = [(header, formatter) for _key, header, formatter in columns]
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels([header for _key, header, _fmt in columns])

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def populate(self, rows: list[dict[str, Any]]) -> None:
        """Replace the table's contents.

        Args:
            rows: The row dicts to display, formatted per
                :meth:`set_columns`.
        """
        self._rows = rows
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, (_header, formatter) in enumerate(self._columns):
                text = formatter(row)
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row_index, column_index, item)

    def selected_row(self) -> dict[str, Any] | None:
        """Return the currently selected row's backing dict, if any.

        Returns:
            The selected row dict, or ``None`` if no row is selected.
        """
        selected = self.table.selectionModel()
        if selected is None or not selected.hasSelection():
            return None
        row_index = selected.selectedRows()[0].row()
        if row_index < 0 or row_index >= len(self._rows):
            return None
        return self._rows[row_index]

    def clear_selection(self) -> None:
        """Clear the table's current selection."""
        self.table.clearSelection()

    # ------------------------------------------------------------------
    # Errors
    # ------------------------------------------------------------------

    def show_error(self, message: str) -> None:
        """Display an inline error banner above the table.

        Args:
            message: The error text to show.
        """
        self.error_label.setText(message)
        self.error_label.show()

    def clear_error(self) -> None:
        """Hide the inline error banner."""
        self.error_label.clear()
        self.error_label.hide()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_cell_double_clicked(self, row_index: int, _column_index: int) -> None:
        """Emit :attr:`row_activated` with the double-clicked row's dict."""
        if 0 <= row_index < len(self._rows):
            self.row_activated.emit(self._rows[row_index])

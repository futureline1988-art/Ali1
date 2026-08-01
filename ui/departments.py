"""Departments screen: manage the company's department hierarchy."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from controllers.department_controller import DepartmentController
from ui.widgets import (
    ConfirmDialog,
    make_danger_button,
    make_heading_label,
    make_primary_button,
    make_status_label,
)

_ROOT_PARENT = None


class DepartmentFormDialog(QDialog):
    """Add/edit form for a single department.

    The parent-department field is only shown in "add" mode: the
    service layer deliberately keeps re-parenting a separate operation
    (:meth:`~services.department_service.DepartmentService.move_department`,
    with its cycle-detection) from a plain field update, so editing an
    existing department here only touches name/code/description.
    """

    def __init__(
        self,
        *,
        parent_choices: list[dict[str, Any]],
        default_parent_id: int | None = None,
        existing: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Build the form dialog.

        Args:
            parent_choices: Every department in this company
                (``{"id", "full_path", ...}`` dicts), for the parent
                picker shown only in "add" mode.
            default_parent_id: Pre-selected parent (e.g. when adding a
                child of a currently-selected tree node).
            existing: The department being edited, or ``None`` to
                create a new one.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._is_edit = existing is not None
        self.setWindowTitle("تعديل قسم" if self._is_edit else "إضافة قسم جديد")
        self.setMinimumWidth(400)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.name_edit = QLineEdit(self)
        form.addRow("اسم القسم *", self.name_edit)

        self.code_edit = QLineEdit(self)
        form.addRow("الرمز", self.code_edit)

        self.description_edit = QPlainTextEdit(self)
        self.description_edit.setFixedHeight(70)
        form.addRow("الوصف", self.description_edit)

        self.parent_combo: QComboBox | None = None
        if not self._is_edit:
            self.parent_combo = QComboBox(self)
            self.parent_combo.addItem("بدون (قسم رئيسي)", userData=_ROOT_PARENT)
            for department in parent_choices:
                self.parent_combo.addItem(department["full_path"], userData=department["id"])
            if default_parent_id is not None:
                index = self.parent_combo.findData(default_parent_id)
                if index >= 0:
                    self.parent_combo.setCurrentIndex(index)
            form.addRow("القسم الأب", self.parent_combo)

        if existing is not None:
            self.name_edit.setText(existing.get("name") or "")
            self.code_edit.setText(existing.get("code") or "")
            self.description_edit.setPlainText(existing.get("description") or "")

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.Save).setText("حفظ")
        buttons.button(QDialogButtonBox.Cancel).setText("إلغاء")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict[str, Any]:
        """Read the form's current state as keyword arguments for the controller.

        Returns:
            A dict compatible with
            :meth:`~controllers.department_controller.DepartmentController.create_department`
            (includes ``parent_department_id``) or ``update_department``
            (does not, in edit mode).
        """
        values: dict[str, Any] = {
            "name": self.name_edit.text().strip(),
            "code": self.code_edit.text().strip() or None,
            "description": self.description_edit.toPlainText().strip() or None,
        }
        if self.parent_combo is not None:
            values["parent_department_id"] = self.parent_combo.currentData()
        return values


class MoveDepartmentDialog(QDialog):
    """A focused dialog for re-parenting one department."""

    def __init__(
        self,
        *,
        department: dict[str, Any],
        parent_choices: list[dict[str, Any]],
        parent: QWidget | None = None,
    ) -> None:
        """Build the move dialog.

        Args:
            department: The department being moved.
            parent_choices: Every department in this company, for the
                new-parent picker (the department itself and its
                current parent are not filtered out here; the service
                layer rejects any resulting cycle).
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle(f"نقل القسم: {department['name']}")
        self.setMinimumWidth(380)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.parent_combo = QComboBox(self)
        self.parent_combo.addItem("بدون (قسم رئيسي)", userData=_ROOT_PARENT)
        for choice in parent_choices:
            if choice["id"] == department["id"]:
                continue
            self.parent_combo.addItem(choice["full_path"], userData=choice["id"])
        current_parent_id = department.get("parent_department_id")
        index = self.parent_combo.findData(current_parent_id)
        if index >= 0:
            self.parent_combo.setCurrentIndex(index)
        form.addRow("القسم الأب الجديد", self.parent_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.Save).setText("نقل")
        buttons.button(QDialogButtonBox.Cancel).setText("إلغاء")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def selected_parent_id(self) -> int | None:
        """The chosen new parent's id, or ``None`` for a root department."""
        return self.parent_combo.currentData()


class DepartmentsPage(QWidget):
    """The department hierarchy management screen."""

    def __init__(self, *, company_id: int, parent: QWidget | None = None) -> None:
        """Create the departments page.

        Args:
            company_id: The company this screen manages departments for.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._controller = DepartmentController(company_id=company_id)
        self._controller.operation_failed.connect(self._show_error)
        self._departments: list[dict[str, Any]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        header_row = QHBoxLayout()
        header_row.addWidget(make_heading_label("الأقسام"))
        header_row.addStretch(1)
        add_root_button = make_primary_button("+ إضافة قسم رئيسي", parent=self)
        add_root_button.clicked.connect(lambda: self._open_add_dialog(default_parent_id=None))
        header_row.addWidget(add_root_button)
        layout.addLayout(header_row)

        self._error_label = make_status_label("", "danger")
        self._error_label.hide()
        layout.addWidget(self._error_label)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["الاسم", "الرمز", "عدد الفروع"])
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.itemDoubleClicked.connect(lambda item, _col: self._open_edit_dialog())
        layout.addWidget(self.tree, stretch=1)

        actions_row = QHBoxLayout()
        self.add_child_button = make_primary_button("+ إضافة قسم فرعي", parent=self)
        self.add_child_button.clicked.connect(self._on_add_child_clicked)
        actions_row.addWidget(self.add_child_button)

        self.edit_button = make_primary_button("تعديل", parent=self)
        self.edit_button.clicked.connect(self._open_edit_dialog)
        actions_row.addWidget(self.edit_button)

        self.move_button = make_primary_button("نقل", parent=self)
        self.move_button.clicked.connect(self._on_move_clicked)
        actions_row.addWidget(self.move_button)

        self.delete_button = make_danger_button("حذف", parent=self)
        self.delete_button.clicked.connect(self._on_delete_clicked)
        actions_row.addWidget(self.delete_button)

        actions_row.addStretch(1)
        layout.addLayout(actions_row)

        self._set_action_buttons_enabled(False)
        self.refresh()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Reload every department and rebuild the tree."""
        self._hide_error()
        self._departments = self._controller.list_all()
        self._rebuild_tree()

    def _rebuild_tree(self) -> None:
        """Rebuild :attr:`tree` from :attr:`_departments`, nested by parent."""
        self.tree.clear()
        by_parent: dict[int | None, list[dict[str, Any]]] = {}
        for department in self._departments:
            by_parent.setdefault(department.get("parent_department_id"), []).append(department)

        def add_children(parent_item: QTreeWidgetItem | None, parent_id: int | None) -> None:
            for department in by_parent.get(parent_id, []):
                item = QTreeWidgetItem(
                    [
                        department["name"],
                        department.get("code") or "",
                        str(department.get("children_count", 0)),
                    ]
                )
                item.setData(0, Qt.UserRole, department)
                if parent_item is None:
                    self.tree.addTopLevelItem(item)
                else:
                    parent_item.addChild(item)
                add_children(item, department["id"])

        add_children(None, _ROOT_PARENT)
        self.tree.expandAll()

    def _selected_department(self) -> dict[str, Any] | None:
        """Return the currently selected tree item's backing department dict."""
        items = self.tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.UserRole)

    def _on_selection_changed(self) -> None:
        """Enable/disable action buttons based on the current selection."""
        self._set_action_buttons_enabled(self._selected_department() is not None)

    def _set_action_buttons_enabled(self, enabled: bool) -> None:
        """Enable/disable every selection-dependent action button."""
        self.add_child_button.setEnabled(enabled)
        self.edit_button.setEnabled(enabled)
        self.move_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Add / edit
    # ------------------------------------------------------------------

    def _open_add_dialog(self, *, default_parent_id: int | None) -> None:
        """Open the "add department" dialog, pre-selecting a parent if given."""
        dialog = DepartmentFormDialog(
            parent_choices=self._departments, default_parent_id=default_parent_id, parent=self
        )
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["name"]:
            self._show_error("اسم القسم حقل إلزامي.")
            return
        result = self._controller.create_department(**values)
        if result is not None:
            self.refresh()

    def _on_add_child_clicked(self) -> None:
        """Open the "add department" dialog with the selected node as parent."""
        selected = self._selected_department()
        if selected is None:
            return
        self._open_add_dialog(default_parent_id=selected["id"])

    def _open_edit_dialog(self) -> None:
        """Open the "edit department" dialog for the selected node."""
        selected = self._selected_department()
        if selected is None:
            return
        dialog = DepartmentFormDialog(
            parent_choices=self._departments, existing=selected, parent=self
        )
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["name"]:
            self._show_error("اسم القسم حقل إلزامي.")
            return
        result = self._controller.update_department(selected["id"], **values)
        if result is not None:
            self.refresh()

    def _on_move_clicked(self) -> None:
        """Open the move dialog for the selected node and apply the new parent."""
        selected = self._selected_department()
        if selected is None:
            return
        dialog = MoveDepartmentDialog(
            department=selected, parent_choices=self._departments, parent=self
        )
        if dialog.exec() != QDialog.Accepted:
            return
        result = self._controller.move_department(
            selected["id"], new_parent_id=dialog.selected_parent_id()
        )
        if result is not None:
            self.refresh()

    def _on_delete_clicked(self) -> None:
        """Confirm and delete the selected department."""
        selected = self._selected_department()
        if selected is None:
            return
        confirmed = ConfirmDialog.confirm(
            self,
            "تأكيد حذف القسم",
            f"هل أنت متأكد من حذف القسم \"{selected['name']}\"؟",
            danger=True,
        )
        if not confirmed:
            return
        if self._controller.delete_department(selected["id"]):
            self.refresh()

    # ------------------------------------------------------------------
    # Errors
    # ------------------------------------------------------------------

    def _show_error(self, message: str) -> None:
        """Display an inline error banner above the tree."""
        self._error_label.setText(message)
        self._error_label.show()

    def _hide_error(self) -> None:
        """Hide the inline error banner."""
        self._error_label.clear()
        self._error_label.hide()

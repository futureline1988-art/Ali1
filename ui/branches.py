"""Branches screen: manage the company's physical branch/location list."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QWidget,
)

from controllers.branch_controller import BranchController
from ui.table_page import TablePage
from ui.widgets import ConfirmDialog, make_danger_button


class BranchFormDialog(QDialog):
    """Add/edit form for a single branch."""

    def __init__(
        self,
        *,
        existing: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Build the form dialog.

        Args:
            existing: The branch being edited, or ``None`` to create a
                new one.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("تعديل فرع" if existing else "إضافة فرع جديد")
        self.setMinimumWidth(400)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.name_edit = QLineEdit(self)
        form.addRow("اسم الفرع *", self.name_edit)

        self.code_edit = QLineEdit(self)
        form.addRow("الرمز", self.code_edit)

        self.address_edit = QLineEdit(self)
        form.addRow("العنوان", self.address_edit)

        self.phone_edit = QLineEdit(self)
        form.addRow("الهاتف", self.phone_edit)

        self.main_branch_checkbox = QCheckBox("الفرع الرئيسي", self)
        form.addRow(self.main_branch_checkbox)

        self.active_checkbox = QCheckBox("فرع نشط", self)
        self.active_checkbox.setChecked(True)
        form.addRow(self.active_checkbox)

        if existing is not None:
            self._apply_existing(existing)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.Save).setText("حفظ")
        buttons.button(QDialogButtonBox.Cancel).setText("إلغاء")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _apply_existing(self, existing: dict[str, Any]) -> None:
        """Pre-fill every field from an existing branch's data."""
        self.name_edit.setText(existing.get("name") or "")
        self.code_edit.setText(existing.get("code") or "")
        self.address_edit.setText(existing.get("address") or "")
        self.phone_edit.setText(existing.get("phone") or "")
        self.main_branch_checkbox.setChecked(bool(existing.get("is_main_branch")))
        self.active_checkbox.setChecked(bool(existing.get("is_active", True)))

    def values(self) -> dict[str, Any]:
        """Read the form's current state as keyword arguments for the controller.

        Returns:
            A dict compatible with
            :meth:`~controllers.branch_controller.BranchController.create_branch`
            / ``update_branch``.
        """
        return {
            "name": self.name_edit.text().strip(),
            "code": self.code_edit.text().strip() or None,
            "address": self.address_edit.text().strip() or None,
            "phone": self.phone_edit.text().strip() or None,
            "is_main_branch": self.main_branch_checkbox.isChecked(),
            "is_active": self.active_checkbox.isChecked(),
        }


class BranchesPage(TablePage):
    """The branch (physical location) management screen."""

    def __init__(
        self,
        *,
        company_id: int,
        current_user_id: int | None = None,
        permission_codes: frozenset[str] = frozenset(),
        parent: QWidget | None = None,
    ) -> None:
        """Create the branches page.

        Args:
            company_id: The company this screen manages branches for.
            current_user_id: The signed-in user, for audit attribution.
            permission_codes: The signed-in user's granted permission
                codes.
            parent: Optional parent widget.
        """
        super().__init__(
            title="الفروع",
            add_button_text="+ إضافة فرع",
            search_placeholder="بحث بالاسم...",
            parent=parent,
        )
        self._controller = BranchController(
            company_id=company_id,
            actor_user_id=current_user_id,
            permission_codes=permission_codes,
        )
        self._controller.operation_failed.connect(self.show_error)

        self.delete_button = make_danger_button("حذف", parent=self)
        self.delete_button.clicked.connect(self._on_delete_clicked)
        self.toolbar_layout.addWidget(self.delete_button)

        self.set_columns(
            [
                ("name", "اسم الفرع", lambda row: row.get("name") or ""),
                ("code", "الرمز", lambda row: row.get("code") or "—"),
                ("address", "العنوان", lambda row: row.get("address") or "—"),
                (
                    "is_main_branch",
                    "الفرع الرئيسي",
                    lambda row: "نعم" if row.get("is_main_branch") else "لا",
                ),
                (
                    "is_active",
                    "نشط",
                    lambda row: "نعم" if row.get("is_active") else "لا",
                ),
            ]
        )

        self.add_requested.connect(self._on_add_clicked)
        self.row_activated.connect(self._on_edit_row)
        self.search_changed.connect(self._on_search_changed)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        self._all_rows: list[dict[str, Any]] = []
        self.refresh()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Reload the branch list from the database."""
        self.clear_error()
        self._all_rows = self._controller.list_branches()
        self.populate(self._all_rows)
        self._on_selection_changed()

    def _on_search_changed(self, query: str) -> None:
        """Filter the currently loaded branches by name (client-side)."""
        self.clear_error()
        rows = self._all_rows
        if query.strip():
            needle = query.strip().lower()
            rows = [row for row in rows if needle in (row.get("name") or "").lower()]
        self.populate(rows)
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        """Enable/disable the delete button based on the current selection."""
        self.delete_button.setEnabled(self.selected_row() is not None)

    # ------------------------------------------------------------------
    # Add / edit / delete
    # ------------------------------------------------------------------

    def _on_add_clicked(self) -> None:
        """Open the "add branch" dialog and persist the result if accepted."""
        dialog = BranchFormDialog(parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["name"]:
            self.show_error("اسم الفرع حقل إلزامي.")
            return
        result = self._controller.create_branch(**values)
        if result is not None:
            self.refresh()

    def _on_edit_row(self, row: dict[str, Any]) -> None:
        """Open the "edit branch" dialog for ``row`` and persist changes."""
        dialog = BranchFormDialog(existing=row, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["name"]:
            self.show_error("اسم الفرع حقل إلزامي.")
            return
        result = self._controller.update_branch(row["id"], **values)
        if result is not None:
            self.refresh()

    def _on_delete_clicked(self) -> None:
        """Confirm and delete the currently selected branch."""
        row = self.selected_row()
        if row is None:
            return
        confirmed = ConfirmDialog.confirm(
            self,
            "تأكيد حذف الفرع",
            f"هل أنت متأكد من حذف فرع \"{row['name']}\"؟",
            danger=True,
        )
        if not confirmed:
            return
        if self._controller.delete_branch(row["id"]):
            self.refresh()

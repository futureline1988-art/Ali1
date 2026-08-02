"""Users screen: manage system user accounts and roles/permissions."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from controllers.user_controller import UserController
from database.database import session_scope
from repositories.permission_repository import PermissionRepository
from ui.table_page import TablePage
from ui.widgets import (
    ConfirmDialog,
    make_danger_button,
    make_heading_label,
    make_primary_button,
    make_secondary_label,
    make_status_label,
)


def _load_all_permissions() -> list[dict[str, Any]]:
    """Fetch the global permission catalog as plain dicts.

    Not company-scoped (every tenant draws from the same fixed
    catalog), so this queries directly rather than through a
    company-bound controller — the same pattern
    ``ui/login_window.py`` uses for the company picker.

    Returns:
        ``{"code", "module", "name_ar", ...}`` dicts, ordered by code;
        an empty list if the catalog has not been seeded yet.
    """
    with session_scope() as session:
        permissions = PermissionRepository(session).list_all()
        return [
            {"code": p.code, "module": p.module, "name_ar": p.name_ar}
            for p in permissions
        ]


class UserFormDialog(QDialog):
    """Add/edit form for a single user account.

    Password is only collected in "add" mode: changing an existing
    user's password is a distinct administrative action
    (:meth:`~controllers.user_controller.UserController.reset_password`),
    not a plain field update.
    """

    def __init__(
        self,
        *,
        roles: list[dict[str, Any]],
        existing: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Build the form dialog.

        Args:
            roles: Available roles (``{"id", "name", ...}`` dicts) for
                the role picker.
            existing: The user being edited, or ``None`` to create a
                new one.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._is_edit = existing is not None
        self.setWindowTitle("تعديل مستخدم" if self._is_edit else "إضافة مستخدم جديد")
        self.setMinimumWidth(400)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.username_edit = QLineEdit(self)
        self.username_edit.setEnabled(not self._is_edit)
        form.addRow("اسم المستخدم *", self.username_edit)

        self.full_name_edit = QLineEdit(self)
        form.addRow("الاسم الكامل *", self.full_name_edit)

        self.email_edit = QLineEdit(self)
        form.addRow("البريد الإلكتروني", self.email_edit)

        self.phone_edit = QLineEdit(self)
        form.addRow("الهاتف", self.phone_edit)

        self.role_combo = QComboBox(self)
        for role in roles:
            self.role_combo.addItem(role["name"], userData=role["id"])
        form.addRow("الدور *", self.role_combo)

        self.password_edit: QLineEdit | None = None
        if not self._is_edit:
            self.password_edit = QLineEdit(self)
            self.password_edit.setEchoMode(QLineEdit.Password)
            form.addRow("كلمة المرور *", self.password_edit)

        self.is_active_checkbox: QCheckBox | None = None
        if self._is_edit:
            self.is_active_checkbox = QCheckBox("الحساب فعال", self)
            self.is_active_checkbox.setChecked(True)
            form.addRow("", self.is_active_checkbox)

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
        """Pre-fill every field from an existing user's data."""
        self.username_edit.setText(existing.get("username") or "")
        self.full_name_edit.setText(existing.get("full_name") or "")
        self.email_edit.setText(existing.get("email") or "")
        self.phone_edit.setText(existing.get("phone") or "")
        role_id = existing.get("role_id")
        index = self.role_combo.findData(role_id)
        if index >= 0:
            self.role_combo.setCurrentIndex(index)
        if self.is_active_checkbox is not None:
            self.is_active_checkbox.setChecked(bool(existing.get("is_active", True)))

    def field_values(self) -> dict[str, Any]:
        """Read the plain-field portion of the form (excludes role/password).

        Returns:
            A dict compatible with
            :meth:`~controllers.user_controller.UserController.update_user`
            (or, merged with ``role_id``/``password``, ``create_user``).
        """
        values: dict[str, Any] = {
            "full_name": self.full_name_edit.text().strip(),
            "email": self.email_edit.text().strip() or None,
            "phone": self.phone_edit.text().strip() or None,
        }
        if self.is_active_checkbox is not None:
            values["is_active"] = self.is_active_checkbox.isChecked()
        return values

    def username(self) -> str:
        """The entered username (only meaningful in "add" mode)."""
        return self.username_edit.text().strip()

    def password(self) -> str:
        """The entered password (only present in "add" mode; empty otherwise)."""
        return self.password_edit.text() if self.password_edit is not None else ""

    def role_id(self) -> int | None:
        """The selected role's id."""
        return self.role_combo.currentData()


class RoleFormDialog(QDialog):
    """Add form for a new custom role."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        """Build the form dialog."""
        super().__init__(parent)
        self.setWindowTitle("إضافة دور جديد")
        self.setMinimumWidth(360)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.name_edit = QLineEdit(self)
        form.addRow("اسم الدور *", self.name_edit)

        self.description_edit = QPlainTextEdit(self)
        self.description_edit.setFixedHeight(70)
        form.addRow("الوصف", self.description_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.Save).setText("حفظ")
        buttons.button(QDialogButtonBox.Cancel).setText("إلغاء")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict[str, Any]:
        """Read the form's current state.

        Returns:
            A dict compatible with
            :meth:`~controllers.user_controller.UserController.create_role`.
        """
        return {
            "name": self.name_edit.text().strip(),
            "description": self.description_edit.toPlainText().strip() or None,
        }


class UsersTab(TablePage):
    """The user-accounts list, nested inside :class:`UsersPage`."""

    def __init__(
        self,
        *,
        company_id: int,
        current_user_id: int | None = None,
        permission_codes: frozenset[str] = frozenset(),
        parent: QWidget | None = None,
    ) -> None:
        """Create the users tab.

        Args:
            company_id: The company this tab manages users for.
            current_user_id: The signed-in user, for audit attribution.
            permission_codes: The signed-in user's granted permission
                codes.
            parent: Optional parent widget.
        """
        super().__init__(
            title="المستخدمون",
            add_button_text="+ إضافة مستخدم",
            search_placeholder="بحث بالاسم...",
            parent=parent,
        )
        self._controller = UserController(
            company_id=company_id,
            actor_user_id=current_user_id,
            permission_codes=permission_codes,
        )
        self._controller.operation_failed.connect(self.show_error)

        self.reset_password_button = make_primary_button("إعادة تعيين كلمة المرور", parent=self)
        self.reset_password_button.setEnabled(False)
        self.reset_password_button.clicked.connect(self._on_reset_password_clicked)
        self.toolbar_layout.addWidget(self.reset_password_button)

        self.delete_button = make_danger_button("حذف", parent=self)
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self._on_delete_clicked)
        self.toolbar_layout.addWidget(self.delete_button)

        self.set_columns(
            [
                ("username", "اسم المستخدم", lambda row: row.get("username") or ""),
                ("full_name", "الاسم الكامل", lambda row: row.get("full_name") or ""),
                ("role_name", "الدور", lambda row: row.get("role_name") or ""),
                ("email", "البريد الإلكتروني", lambda row: row.get("email") or "—"),
                (
                    "is_active",
                    "الحالة",
                    lambda row: "فعال" if row.get("is_active") else "معطل",
                ),
            ]
        )

        self.add_requested.connect(self._on_add_clicked)
        self.row_activated.connect(self._on_edit_row)
        self.search_changed.connect(self._on_search_changed)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        self._all_rows: list[dict[str, Any]] = []
        self.refresh()

    def refresh(self) -> None:
        """Reload the user list from the database."""
        self.clear_error()
        self._all_rows = self._controller.list_users()
        self.populate(self._all_rows)
        self._on_selection_changed()

    def _on_search_changed(self, query: str) -> None:
        """Filter the loaded users by name/username (client-side)."""
        self.clear_error()
        if not query.strip():
            self.populate(self._all_rows)
        else:
            needle = query.strip().lower()
            filtered = [
                row
                for row in self._all_rows
                if needle in (row.get("full_name") or "").lower()
                or needle in (row.get("username") or "").lower()
            ]
            self.populate(filtered)
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        """Enable/disable selection-dependent buttons."""
        has_selection = self.selected_row() is not None
        self.reset_password_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)

    def _load_roles(self) -> list[dict[str, Any]]:
        """Fetch every role in this company, for the user form's role picker."""
        return self._controller.list_roles()

    def _on_add_clicked(self) -> None:
        """Open the "add user" dialog and persist the result if accepted."""
        dialog = UserFormDialog(roles=self._load_roles(), parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        if not dialog.username() or not dialog.field_values()["full_name"] or not dialog.password():
            self.show_error("اسم المستخدم والاسم الكامل وكلمة المرور حقول إلزامية.")
            return
        if dialog.role_id() is None:
            self.show_error("الرجاء اختيار الدور.")
            return
        result = self._controller.create_user(
            username=dialog.username(),
            full_name=dialog.field_values()["full_name"],
            password=dialog.password(),
            role_id=dialog.role_id(),
            email=dialog.field_values()["email"],
            phone=dialog.field_values()["phone"],
        )
        if result is not None:
            self.refresh()

    def _on_edit_row(self, row: dict[str, Any]) -> None:
        """Open the "edit user" dialog for ``row`` and persist changes."""
        dialog = UserFormDialog(roles=self._load_roles(), existing=row, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.field_values()
        if not values["full_name"]:
            self.show_error("الاسم الكامل حقل إلزامي.")
            return
        updated = self._controller.update_user(row["id"], **values)
        if updated is None:
            return
        new_role_id = dialog.role_id()
        if new_role_id is not None and new_role_id != row.get("role_id"):
            self._controller.change_user_role(row["id"], new_role_id)
        self.refresh()

    def _on_reset_password_clicked(self) -> None:
        """Prompt for a new password and administratively reset the selected user's."""
        row = self.selected_row()
        if row is None:
            return
        new_password, accepted = QInputDialog.getText(
            self, "إعادة تعيين كلمة المرور", "كلمة المرور الجديدة", QLineEdit.Password
        )
        if not accepted or not new_password:
            return
        if self._controller.reset_password(row["id"], new_password):
            self.refresh()

    def _on_delete_clicked(self) -> None:
        """Confirm and delete the currently selected user."""
        row = self.selected_row()
        if row is None:
            return
        confirmed = ConfirmDialog.confirm(
            self,
            "تأكيد حذف المستخدم",
            f"هل أنت متأكد من حذف المستخدم \"{row['full_name']}\"؟",
            danger=True,
        )
        if not confirmed:
            return
        if self._controller.delete_user(row["id"]):
            self.refresh()


class RolesTab(QWidget):
    """The roles + permissions editor, nested inside :class:`UsersPage`."""

    def __init__(
        self,
        *,
        company_id: int,
        current_user_id: int | None = None,
        permission_codes: frozenset[str] = frozenset(),
        parent: QWidget | None = None,
    ) -> None:
        """Create the roles tab.

        Args:
            company_id: The company this tab manages roles for.
            current_user_id: The signed-in user, for audit attribution.
            permission_codes: The signed-in user's granted permission
                codes.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._controller = UserController(
            company_id=company_id,
            actor_user_id=current_user_id,
            permission_codes=permission_codes,
        )
        self._controller.operation_failed.connect(self._show_error)
        self._all_permissions = _load_all_permissions()
        self._roles: list[dict[str, Any]] = []
        self._permission_items: dict[str, QListWidgetItem] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        self._error_label = make_status_label("", "danger")
        self._error_label.hide()
        layout.addWidget(self._error_label)

        splitter = QSplitter(self)

        roles_panel = QWidget(splitter)
        roles_layout = QVBoxLayout(roles_panel)
        roles_layout.addWidget(make_heading_label("الأدوار"))
        self.roles_list = QListWidget(roles_panel)
        self.roles_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.roles_list.currentItemChanged.connect(self._on_role_selected)
        roles_layout.addWidget(self.roles_list)

        roles_buttons = QHBoxLayout()
        add_role_button = make_primary_button("+ إضافة دور", parent=roles_panel)
        add_role_button.clicked.connect(self._on_add_role_clicked)
        roles_buttons.addWidget(add_role_button)
        self.delete_role_button = make_danger_button("حذف الدور", parent=roles_panel)
        self.delete_role_button.setEnabled(False)
        self.delete_role_button.clicked.connect(self._on_delete_role_clicked)
        roles_buttons.addWidget(self.delete_role_button)
        roles_layout.addLayout(roles_buttons)

        permissions_panel = QWidget(splitter)
        permissions_layout = QVBoxLayout(permissions_panel)
        permissions_layout.addWidget(make_heading_label("الصلاحيات"))
        self.permissions_hint = make_secondary_label("اختر دورًا لعرض صلاحياته.")
        permissions_layout.addWidget(self.permissions_hint)
        self.permissions_list = QListWidget(permissions_panel)
        permissions_layout.addWidget(self.permissions_list)
        self.save_permissions_button = make_primary_button(
            "حفظ الصلاحيات", parent=permissions_panel
        )
        self.save_permissions_button.setEnabled(False)
        self.save_permissions_button.clicked.connect(self._on_save_permissions_clicked)
        permissions_layout.addWidget(self.save_permissions_button)

        layout.addWidget(splitter, stretch=1)

        self.refresh()

    def refresh(self) -> None:
        """Reload every role and rebuild the roles list."""
        self._hide_error()
        self._roles = self._controller.list_roles()
        self.roles_list.clear()
        for role in self._roles:
            suffix = " (نظامي)" if role.get("is_system_role") else ""
            item = QListWidgetItem(f"{role['name']}{suffix}")
            item.setData(Qt.UserRole, role)
            self.roles_list.addItem(item)
        self._render_permissions(None)

    def _on_role_selected(self, current: QListWidgetItem | None, _previous) -> None:
        """Render the selected role's permission checklist."""
        role = current.data(Qt.UserRole) if current is not None else None
        self.delete_role_button.setEnabled(
            role is not None and not role.get("is_system_role", False)
        )
        self._render_permissions(role)

    def _render_permissions(self, role: dict[str, Any] | None) -> None:
        """Rebuild the permission checklist for ``role`` (or clear it if ``None``)."""
        self.permissions_list.clear()
        self._permission_items.clear()
        self.save_permissions_button.setEnabled(role is not None)

        if role is None:
            self.permissions_hint.setText("اختر دورًا لعرض صلاحياته.")
            self.permissions_hint.show()
            return

        if not self._all_permissions:
            self.permissions_hint.setText("لا توجد صلاحيات معرفة في النظام بعد.")
            self.permissions_hint.show()
            return

        self.permissions_hint.hide()
        granted_codes = set(role.get("permission_codes") or [])
        for permission in self._all_permissions:
            item = QListWidgetItem(f"{permission['name_ar']} ({permission['code']})")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Checked if permission["code"] in granted_codes else Qt.Unchecked
            )
            item.setData(Qt.UserRole, permission["code"])
            self.permissions_list.addItem(item)
            self._permission_items[permission["code"]] = item

    def _current_role(self) -> dict[str, Any] | None:
        """The currently selected role's dict, if any."""
        item = self.roles_list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _on_add_role_clicked(self) -> None:
        """Open the "add role" dialog and persist the result if accepted."""
        dialog = RoleFormDialog(parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["name"]:
            self._show_error("اسم الدور حقل إلزامي.")
            return
        result = self._controller.create_role(**values)
        if result is not None:
            self.refresh()

    def _on_delete_role_clicked(self) -> None:
        """Confirm and delete the selected (non-system) role."""
        role = self._current_role()
        if role is None:
            return
        confirmed = ConfirmDialog.confirm(
            self,
            "تأكيد حذف الدور",
            f"هل أنت متأكد من حذف الدور \"{role['name']}\"؟",
            danger=True,
        )
        if not confirmed:
            return
        if self._controller.delete_role(role["id"]):
            self.refresh()

    def _on_save_permissions_clicked(self) -> None:
        """Persist the checklist's current state as the selected role's permission set."""
        role = self._current_role()
        if role is None:
            return
        codes = [
            code
            for code, item in self._permission_items.items()
            if item.checkState() == Qt.Checked
        ]
        result = self._controller.update_role_permissions(role["id"], codes)
        if result is not None:
            self.refresh()

    def _show_error(self, message: str) -> None:
        """Display an inline error banner."""
        self._error_label.setText(message)
        self._error_label.show()

    def _hide_error(self) -> None:
        """Hide the inline error banner."""
        self._error_label.clear()
        self._error_label.hide()


class UsersPage(QTabWidget):
    """The users + roles/permissions management screen."""

    def __init__(
        self,
        *,
        company_id: int,
        current_user_id: int | None = None,
        permission_codes: frozenset[str] = frozenset(),
        parent: QWidget | None = None,
    ) -> None:
        """Create the users page.

        Args:
            company_id: The company this screen manages users for.
            current_user_id: The signed-in user, for audit attribution.
            permission_codes: The signed-in user's granted permission
                codes.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.users_tab = UsersTab(
            company_id=company_id,
            current_user_id=current_user_id,
            permission_codes=permission_codes,
            parent=self,
        )
        self.roles_tab = RolesTab(
            company_id=company_id,
            current_user_id=current_user_id,
            permission_codes=permission_codes,
            parent=self,
        )
        self.addTab(self.users_tab, "المستخدمون")
        self.addTab(self.roles_tab, "الأدوار والصلاحيات")

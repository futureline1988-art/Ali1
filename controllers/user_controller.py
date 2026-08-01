"""User controller: bridges the users/roles administration screen to ``UserService``."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from sqlalchemy.orm import Session

from controllers.base_controller import BaseController
from models.role import Role
from models.user import User
from repositories.role_repository import RoleRepository
from repositories.user_repository import UserRepository
from services.user_service import UserService


def _user_to_dict(user: User) -> dict[str, Any]:
    """Serialize a user plus its role name, while the session is open."""
    data = user.to_dict(exclude={"password_hash"})
    data["role_name"] = user.role_name
    return data


def _role_to_dict(role: Role) -> dict[str, Any]:
    """Serialize a role plus its granted permission codes, while the session is open."""
    data = role.to_dict()
    data["permission_codes"] = [permission.code for permission in role.permissions]
    return data


class UserController(BaseController):
    """Controller for the system users and roles administration screen."""

    users_changed = Signal()
    """Emitted after any successful user create/update/delete."""

    roles_changed = Signal()
    """Emitted after any successful role create/update/delete."""

    def create_user(
        self,
        *,
        username: str,
        full_name: str,
        password: str,
        role_id: int,
        email: str | None = None,
        phone: str | None = None,
    ) -> dict[str, Any] | None:
        """Create a new system user account.

        Args mirror :meth:`~services.user_service.UserService.create_user`.

        Returns:
            The new user's data as a dict, or ``None`` on failure.
        """

        def do_create(session: Session) -> dict[str, Any]:
            service = UserService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            user = service.create_user(
                username=username,
                full_name=full_name,
                password=password,
                role_id=role_id,
                email=email,
                phone=phone,
            )
            return _user_to_dict(user)

        result = self._run(do_create)
        if result is not None:
            self.users_changed.emit()
        return result

    def update_user(self, user_id: int, **fields: Any) -> dict[str, Any] | None:
        """Update an existing user's editable fields.

        Args:
            user_id: The user to update.
            **fields: See :meth:`~services.user_service.UserService.update_user`.

        Returns:
            The updated user's data as a dict, or ``None`` on failure.
        """

        def do_update(session: Session) -> dict[str, Any]:
            repo = UserRepository(session, company_id=self.company_id)
            user = repo.get_by_id(user_id)
            if user is None:
                raise ValueError(f"User {user_id!r} was not found.")
            service = UserService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            updated = service.update_user(user, **fields)
            return _user_to_dict(updated)

        result = self._run(do_update)
        if result is not None:
            self.users_changed.emit()
        return result

    def change_user_role(self, user_id: int, role_id: int) -> dict[str, Any] | None:
        """Change a user's assigned role.

        Args:
            user_id: The user to update.
            role_id: The new role.

        Returns:
            The updated user's data as a dict, or ``None`` on failure.
        """

        def do_change(session: Session) -> dict[str, Any]:
            repo = UserRepository(session, company_id=self.company_id)
            user = repo.get_by_id(user_id)
            if user is None:
                raise ValueError(f"User {user_id!r} was not found.")
            service = UserService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            updated = service.change_user_role(user, role_id)
            return _user_to_dict(updated)

        result = self._run(do_change)
        if result is not None:
            self.users_changed.emit()
        return result

    def reset_password(self, user_id: int, new_password: str) -> bool:
        """Administratively reset a user's password.

        Args:
            user_id: The user whose password is being reset.
            new_password: The new plaintext password.

        Returns:
            ``True`` on success, ``False`` on failure.
        """

        def do_reset(session: Session) -> bool:
            repo = UserRepository(session, company_id=self.company_id)
            user = repo.get_by_id(user_id)
            if user is None:
                raise ValueError(f"User {user_id!r} was not found.")
            service = UserService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            service.reset_password(user, new_password)
            return True

        return bool(self._run(do_reset))

    def delete_user(self, user_id: int) -> bool:
        """Soft-delete a user account.

        Args:
            user_id: The user to delete.

        Returns:
            ``True`` on success, ``False`` on failure.
        """

        def do_delete(session: Session) -> bool:
            repo = UserRepository(session, company_id=self.company_id)
            user = repo.get_by_id(user_id)
            if user is None:
                raise ValueError(f"User {user_id!r} was not found.")
            service = UserService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            service.delete_user(user)
            return True

        result = self._run(do_delete)
        if result:
            self.users_changed.emit()
        return bool(result)

    def list_users(self) -> list[dict[str, Any]]:
        """List every user in this company.

        Returns:
            Users' data as dicts; an empty list on failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            service = UserService(session, company_id=self.company_id)
            return [_user_to_dict(user) for user in service.list_users()]

        return self._run(do_list) or []

    def create_role(
        self, *, name: str, description: str | None = None
    ) -> dict[str, Any] | None:
        """Create a new custom role.

        Args:
            name: Role display name.
            description: Optional explanation.

        Returns:
            The new role's data as a dict, or ``None`` on failure.
        """

        def do_create(session: Session) -> dict[str, Any]:
            service = UserService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            role = service.create_role(name=name, description=description)
            return _role_to_dict(role)

        result = self._run(do_create)
        if result is not None:
            self.roles_changed.emit()
        return result

    def update_role_permissions(
        self, role_id: int, permission_codes: list[str]
    ) -> dict[str, Any] | None:
        """Replace a role's granted permission set.

        Args:
            role_id: The role to update.
            permission_codes: The full new set of permission codes.

        Returns:
            The updated role's data as a dict, or ``None`` on failure.
        """

        def do_update(session: Session) -> dict[str, Any]:
            repo = RoleRepository(session, company_id=self.company_id)
            role = repo.get_by_id(role_id)
            if role is None:
                raise ValueError(f"Role {role_id!r} was not found.")
            service = UserService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            updated = service.update_role_permissions(role, permission_codes)
            return _role_to_dict(updated)

        result = self._run(do_update)
        if result is not None:
            self.roles_changed.emit()
        return result

    def delete_role(self, role_id: int) -> bool:
        """Delete a custom (non-built-in) role.

        Args:
            role_id: The role to delete.

        Returns:
            ``True`` on success, ``False`` on failure (including an
            attempt to delete a built-in system role).
        """

        def do_delete(session: Session) -> bool:
            repo = RoleRepository(session, company_id=self.company_id)
            role = repo.get_by_id(role_id)
            if role is None:
                raise ValueError(f"Role {role_id!r} was not found.")
            service = UserService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            service.delete_role(role)
            return True

        result = self._run(do_delete)
        if result:
            self.roles_changed.emit()
        return bool(result)

    def list_roles(self) -> list[dict[str, Any]]:
        """List every role in this company.

        Returns:
            Roles' data as dicts; an empty list on failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            service = UserService(session, company_id=self.company_id)
            return [_role_to_dict(role) for role in service.list_roles()]

        return self._run(do_list) or []

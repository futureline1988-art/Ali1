"""System user and role administration service.

Distinct from ``services/auth_service.py``, which owns login/logout/
session concerns — this service owns *administrative* management of
accounts and their access rights: creating a new user, resetting a
forgotten password, changing a user's role, and configuring what a
role can do (its granted :class:`~models.permission.Permission` set).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from models.audit_log import AuditLog
from models.enums import AuditAction
from models.role import Role
from models.user import User
from repositories.audit_log_repository import AuditLogRepository
from repositories.permission_repository import PermissionRepository
from repositories.role_repository import RoleRepository
from repositories.user_repository import UserRepository
from utils.security import hash_password, validate_password_strength
from utils.validators import is_valid_email, is_valid_phone, is_valid_username, is_within_length

_UPDATABLE_USER_FIELDS = frozenset(
    {"full_name", "email", "phone", "preferred_language", "is_active"}
)


class UserManagementError(Exception):
    """Raised when user/role administration input fails validation."""


class UserService:
    """System user and role administration scoped to one company.

    Attributes:
        session: The active database session.
        company_id: The company this service operates within.
        actor_user_id: The user performing these operations, recorded
            on every audit log entry.
    """

    def __init__(
        self, session: Session, *, company_id: int, actor_user_id: int | None = None
    ) -> None:
        """Create a user service bound to one session and company.

        Args:
            session: The active database session.
            company_id: The company to operate within.
            actor_user_id: The acting user's id, for audit attribution.
        """
        self.session = session
        self.company_id = company_id
        self.actor_user_id = actor_user_id
        self.user_repo = UserRepository(session, company_id=company_id)
        self.role_repo = RoleRepository(session, company_id=company_id)
        self.permission_repo = PermissionRepository(session)
        self.audit_repo = AuditLogRepository(session)

    def create_user(
        self,
        *,
        username: str,
        full_name: str,
        password: str,
        role_id: int,
        email: str | None = None,
        phone: str | None = None,
    ) -> User:
        """Create a new system user account.

        Args:
            username: Unique-per-company login handle.
            full_name: Display name.
            password: Plaintext initial password (hashed before storage).
            role_id: The role to grant, which must belong to this
                company.
            email: Contact/recovery email, if provided.
            phone: Contact phone number, if provided.

        Returns:
            The newly created, persisted user.

        Raises:
            UserManagementError: If any field fails format validation,
                the password fails strength requirements, ``role_id``
                does not resolve to a role in this company, or
                ``username``/``email`` is already in use in this
                company.
        """
        if not is_valid_username(username):
            raise UserManagementError(f"Invalid username format: {username!r}.")
        if not is_within_length(full_name, minimum=2, maximum=150):
            raise UserManagementError("Full name must be 2-150 characters.")
        if email and not is_valid_email(email):
            raise UserManagementError(f"Invalid email format: {email!r}.")
        if phone and not is_valid_phone(phone):
            raise UserManagementError(f"Invalid phone number format: {phone!r}.")
        violations = validate_password_strength(password)
        if violations:
            raise UserManagementError(" ".join(violations))
        if self.user_repo.get_by_username(username) is not None:
            raise UserManagementError(f"Username {username!r} is already in use.")
        if email and self.user_repo.get_by_email(email) is not None:
            raise UserManagementError(f"Email {email!r} is already in use.")
        if self.role_repo.get_by_id(role_id) is None:
            raise UserManagementError(f"Role {role_id!r} was not found in this company.")

        user = User(
            company_id=self.company_id,
            username=username,
            full_name=full_name,
            password_hash=hash_password(password),
            role_id=role_id,
            email=email,
            phone=phone,
            created_by_id=self.actor_user_id,
        )
        self.user_repo.add(user)

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.CREATE,
                entity_type="User",
                entity_id=user.id,
                description=f"Created user {username!r}.",
            )
        )
        return user

    def create_bootstrap_admin(
        self, *, username: str, full_name: str, password_hash: str, role_id: int
    ) -> User:
        """Materialize a company's initial administrator from an already-hashed password.

        The one and only path that ever creates a company's very first
        user: called by
        :meth:`~services.subscription_check_service.SubscriptionCheckService._bootstrap_initial_admin`
        right after this installation's first-ever successful
        enrollment, from the bcrypt hash downloaded via
        :meth:`~sync.coordinator.ClientSyncCoordinator.get_initial_admin`
        -- never from a plaintext password typed into the Attendance
        Client, and never through :meth:`create_user`, since that
        method always hashes a fresh plaintext password itself. See
        :mod:`server.models.initial_admin`'s own docstring for why the
        Attendance Client must never create this account any other
        way. A bcrypt hash is self-contained and independently
        verifiable by :func:`~utils.security.verify_password` no
        matter which process computed it, so the hash the Attendance
        Server produced can be stored here and later verified by this
        installation's own
        :meth:`~services.auth_service.AuthService.login` without ever
        re-hashing it.

        Args:
            username: The administrator's login handle, exactly as set
                in the Developer Suite.
            full_name: Display name, exactly as set in the Developer
                Suite.
            password_hash: An already-computed bcrypt hash -- never a
                plaintext password.
            role_id: The role to grant -- the caller resolves this
                company's seeded ``system_admin`` role (see
                :class:`~models.enums.UserRole`).

        Returns:
            The newly created, persisted user.

        Raises:
            UserManagementError: If ``username`` is malformed, or
                ``role_id`` does not resolve to a role in this
                company, or ``username`` is already in use in this
                company (should not happen for a brand-new
                installation's very first user, but guarded the same
                way :meth:`create_user` is).
        """
        if not is_valid_username(username):
            raise UserManagementError(f"Invalid username format: {username!r}.")
        if not is_within_length(full_name, minimum=2, maximum=150):
            raise UserManagementError("Full name must be 2-150 characters.")
        if self.user_repo.get_by_username(username) is not None:
            raise UserManagementError(f"Username {username!r} is already in use.")
        if self.role_repo.get_by_id(role_id) is None:
            raise UserManagementError(f"Role {role_id!r} was not found in this company.")

        user = User(
            company_id=self.company_id,
            username=username,
            full_name=full_name,
            password_hash=password_hash,
            role_id=role_id,
            created_by_id=self.actor_user_id,
        )
        self.user_repo.add(user)

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.CREATE,
                entity_type="User",
                entity_id=user.id,
                description=f"Bootstrapped initial administrator {username!r} from the Developer Suite.",
            )
        )
        return user

    def update_user(self, user: User, **fields: Any) -> User:
        """Update an existing user's editable fields.

        Args:
            user: The user to update (must belong to this service's
                company).
            **fields: Any subset of ``full_name``/``email``/``phone``/
                ``preferred_language``/``is_active``.

        Returns:
            The updated user.

        Raises:
            UserManagementError: If a provided field fails validation,
                or a provided ``email`` collides with a different user
                in this company.
        """
        if user.company_id != self.company_id:
            raise UserManagementError("This user does not belong to the current company.")
        if fields.get("email") and not is_valid_email(fields["email"]):
            raise UserManagementError(f"Invalid email format: {fields['email']!r}.")
        if fields.get("phone") and not is_valid_phone(fields["phone"]):
            raise UserManagementError(f"Invalid phone number format: {fields['phone']!r}.")
        if fields.get("email"):
            existing = self.user_repo.get_by_email(fields["email"])
            if existing is not None and existing.id != user.id:
                raise UserManagementError(f"Email {fields['email']!r} is already in use.")

        user.update_from_dict(fields, allowed_fields=_UPDATABLE_USER_FIELDS)
        user.updated_by_id = self.actor_user_id
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="User",
                entity_id=user.id,
                description=f"Updated user {user.username!r}.",
                changes={key: str(value) for key, value in fields.items()},
            )
        )
        return user

    def change_user_role(self, user: User, role_id: int) -> User:
        """Change a user's assigned role.

        Args:
            user: The user to update.
            role_id: The new role, which must belong to this company.

        Returns:
            The updated user.

        Raises:
            UserManagementError: If ``role_id`` does not resolve to a
                role in this company.
        """
        if self.role_repo.get_by_id(role_id) is None:
            raise UserManagementError(f"Role {role_id!r} was not found in this company.")
        previous_role_id = user.role_id
        user.role_id = role_id
        user.updated_by_id = self.actor_user_id
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="User",
                entity_id=user.id,
                description=(
                    f"Changed role of user {user.username!r} from role "
                    f"{previous_role_id!r} to {role_id!r}."
                ),
            )
        )
        return user

    def reset_password(self, user: User, new_password: str) -> User:
        """Administratively reset a user's password.

        Unlike :meth:`~services.auth_service.AuthService.change_password`,
        this does not require the user's current password (an
        administrator action, e.g. for a forgotten password) and forces
        a password change on the user's next login.

        Args:
            user: The user whose password is being reset.
            new_password: The new plaintext password.

        Returns:
            The updated user.

        Raises:
            UserManagementError: If ``new_password`` fails strength
                requirements.
        """
        violations = validate_password_strength(new_password)
        if violations:
            raise UserManagementError(" ".join(violations))

        user.password_hash = hash_password(new_password)
        user.must_change_password = True
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="User",
                entity_id=user.id,
                description=f"Administratively reset password for {user.username!r}.",
            )
        )
        return user

    def delete_user(self, user: User) -> None:
        """Soft-delete a user account.

        Args:
            user: The user to remove from active views.
        """
        self.user_repo.delete(user)
        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.DELETE,
                entity_type="User",
                entity_id=user.id,
                description=f"Deleted user {user.username!r}.",
            )
        )

    def list_users(self) -> list[User]:
        """List every user in this company.

        Returns:
            This company's users.
        """
        return self.user_repo.list_all()

    def create_role(
        self, *, name: str, code: str | None = None, description: str | None = None
    ) -> Role:
        """Create a new custom role.

        Args:
            name: Role display name; unique within this company.
            code: Optional stable identifier; leave unset for a custom
                (non-built-in) role.
            description: Optional explanation of the role's purpose.

        Returns:
            The newly created role.

        Raises:
            UserManagementError: If ``name`` fails length validation or
                is already in use in this company.
        """
        if not is_within_length(name, minimum=2, maximum=150):
            raise UserManagementError("Role name must be 2-150 characters.")
        if self.role_repo.get_by_name(name) is not None:
            raise UserManagementError(f"Role name {name!r} is already in use.")

        role = Role(
            company_id=self.company_id, name=name, code=code, description=description
        )
        self.role_repo.add(role)
        return role

    def update_role_permissions(self, role: Role, permission_codes: list[str]) -> Role:
        """Replace a role's granted permission set.

        Args:
            role: The role to update (must belong to this service's
                company).
            permission_codes: The full new set of
                :attr:`~models.permission.Permission.code` values this
                role should grant (replaces, not merges with, the
                current set).

        Returns:
            The updated role.

        Raises:
            UserManagementError: If ``role`` belongs to a different
                company, or any code in ``permission_codes`` does not
                exist in the global permission catalog.
        """
        if role.company_id != self.company_id:
            raise UserManagementError("This role does not belong to the current company.")

        permissions = []
        for code in permission_codes:
            permission = self.permission_repo.get_by_code(code)
            if permission is None:
                raise UserManagementError(f"Unknown permission code: {code!r}.")
            permissions.append(permission)

        role.permissions = permissions
        self.session.flush()
        return role

    def delete_role(self, role: Role) -> None:
        """Delete a custom role.

        Args:
            role: The role to delete.

        Raises:
            UserManagementError: If ``role`` is a built-in system role
                (:attr:`~models.role.Role.is_system_role`).
        """
        if role.is_system_role:
            raise UserManagementError("Built-in system roles cannot be deleted.")
        self.role_repo.delete(role)

    def list_roles(self) -> list[Role]:
        """List every role in this company.

        Returns:
            This company's roles.
        """
        return self.role_repo.list_all()

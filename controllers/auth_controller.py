"""Authentication controller: bridges the login/lock-screen UI to ``AuthService``.

A user must already know which company they belong to before logging
in — usernames are unique per company, not globally (see
:class:`~models.user.User`) — so this controller is constructed with a
fixed ``company_id`` just like every other controller; the login
window is responsible for letting the user pick/confirm their company
first (e.g. a remembered last-used company, or a picker).
"""

from __future__ import annotations

from PySide6.QtCore import Signal

from controllers.base_controller import BaseController
from repositories.user_repository import UserRepository
from services.auth_service import AuthenticationError, AuthService
from utils.security import SessionManager


class AuthController(BaseController):
    """Controller for the login screen and session lifecycle.

    Attributes:
        session_manager: The desktop session tracker updated on
            successful login/logout.
    """

    login_succeeded = Signal(dict)
    """Emitted with the authenticated user's data (as a dict) on success."""

    logged_out = Signal()
    """Emitted after a successful logout."""

    def __init__(
        self, *, company_id: int, session_manager: SessionManager | None = None
    ) -> None:
        """Create an auth controller.

        Args:
            company_id: The company to authenticate against.
            session_manager: The desktop session tracker to update; a
                new one is created if omitted.
        """
        super().__init__(company_id=company_id)
        self.session_manager = session_manager or SessionManager()

    def login(self, username: str, password: str) -> dict | None:
        """Attempt to log in, emitting :attr:`login_succeeded` on success.

        Args:
            username: The login handle.
            password: The plaintext password.

        Returns:
            The authenticated user's data as a dict (password hash
            excluded), or ``None`` if authentication failed — see
            :attr:`~controllers.base_controller.BaseController.operation_failed`
            for the reason.
        """

        def do_login(session):
            service = AuthService(
                session, company_id=self.company_id, session_manager=self.session_manager
            )
            user = service.login(username, password)
            self.actor_user_id = user.id
            data = user.to_dict(exclude={"password_hash"})
            data["role_name"] = user.role_name
            return data

        result = self._run(do_login)
        if result is not None:
            self.login_succeeded.emit(result)
        return result

    def logout(self) -> None:
        """Log the current user out, emitting :attr:`logged_out`."""
        user_id = self.session_manager.current_user_id
        if user_id is None:
            return

        def do_logout(session):
            user_repo = UserRepository(session, company_id=self.company_id)
            user = user_repo.get_by_id(user_id)
            if user is None:
                return
            service = AuthService(
                session, company_id=self.company_id, session_manager=self.session_manager
            )
            service.logout(user)

        self._run(do_logout)
        self.logged_out.emit()

    def change_password(
        self, *, user_id: int, current_password: str, new_password: str
    ) -> bool:
        """Change a user's own password.

        Args:
            user_id: The user changing their password.
            current_password: Their current plaintext password, for
                verification.
            new_password: The new plaintext password to set.

        Returns:
            ``True`` on success, ``False`` on failure (see
            :attr:`~controllers.base_controller.BaseController.operation_failed`
            for the reason).
        """

        def do_change(session):
            user_repo = UserRepository(session, company_id=self.company_id)
            user = user_repo.get_by_id(user_id)
            if user is None:
                raise AuthenticationError("User not found.")
            service = AuthService(session, company_id=self.company_id)
            service.change_password(
                user, current_password=current_password, new_password=new_password
            )
            return True

        return bool(self._run(do_change))

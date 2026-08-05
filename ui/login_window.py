"""Login screen: company code (first run only) + username/password authentication.

This server is multi-tenant, so this screen never lists or lets a user
pick from other companies (see
:meth:`~services.subscription_check_service.SubscriptionCheckService.resolve_company_code`'s
own docstring) — instead, a brand-new installation asks for a company
code (handed to that company's employees by their administrator, out
of band) alongside the usual username/password. That code is resolved
against the Attendance Server *before* local authentication runs (only
the server knows which company a code belongs to); only once local
authentication then succeeds does this installation permanently bind
itself to that company, and every later login skips the code prompt
entirely (see :func:`_load_bound_company`).

Usernames are unique per company, not globally (see
:class:`~models.user.User`), so local authentication is always scoped
to one company id — resolved either from the bound company (repeat
logins) or from the just-resolved company code's name (first login).
This window queries the local company/binding state directly through
:func:`~database.database.session_scope` (there is deliberately no
company-scoped controller for this — it is the one screen in the app
that runs *before* a company is known), then delegates the actual
login attempt to a freshly-constructed
:class:`~controllers.auth_controller.AuthController` bound to the
resolved company.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QSpacerItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from controllers.auth_controller import AuthController
from database.database import session_scope
from repositories.sync_repository import ClientSyncCredentialRepository
from services.company_service import CompanyService
from services.subscription_check_service import SubscriptionCheckService
from ui.widgets import make_heading_label, make_primary_button, make_secondary_label
from utils.security import SessionManager


def _load_bound_company() -> dict[str, Any] | None:
    """Return this installation's permanently bound company, if any.

    ``None`` before this installation's first-ever successful login
    -driven enrollment (see
    :meth:`~services.subscription_check_service.SubscriptionCheckService.confirm_local_binding`,
    driven by this window's own :meth:`LoginWindow._attempt_login`) —
    from that point on, this device serves exactly one company, so the
    company code is never asked for again.
    """
    with session_scope() as session:
        bound_company_id = ClientSyncCredentialRepository(session).get_bound_company_id()
        if bound_company_id is None:
            return None
        service = CompanyService(session)
        company = service.company_repo.get_by_id(bound_company_id)
        if company is None:
            return None
        return {"id": company.id, "name": company.name}


class LoginWindow(QWidget):
    """The application's entry screen.

    Emits :attr:`login_successful` once a user has been authenticated;
    ``main.py`` is responsible for constructing and showing the main
    window in response and closing/hiding this one.
    """

    login_successful = Signal(dict, int)
    """Emitted with ``(user_dict, company_id)`` on a successful login."""

    def __init__(
        self,
        *,
        subscription_check_service: SubscriptionCheckService,
        session_manager: SessionManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Build the login screen, showing the company code field only if not yet bound.

        Args:
            subscription_check_service: Resolves a typed company code
                with the Attendance Server (first login only) and, on
                a subsequent successful local login, permanently binds
                this installation to the resolved company — see
                :meth:`SubscriptionCheckService.resolve_company_code`/
                :meth:`SubscriptionCheckService.confirm_local_binding`.
            session_manager: The desktop session tracker to authenticate
                into; defaults to a new, private
                :class:`~utils.security.SessionManager` (fine for
                standalone use/tests). The composition root
                (``main.py``) should inject the same instance it later
                hands to :class:`~ui.main_window.MainWindow`, so the
                session's idle-timeout clock actually starts at login
                rather than being silently discarded.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("تسجيل الدخول - نظام إدارة الحضور والانصراف")
        self.setMinimumSize(920, 560)

        self.session_manager = session_manager or SessionManager()
        self._subscription_check_service = subscription_check_service
        self._auth_controller: AuthController | None = None
        self._password_visible = False
        self._did_succeed = False
        self._pending_company_code: str | None = None
        self._bound_company: dict[str, Any] | None = None

        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_branding_panel(), stretch=1)
        root_layout.addWidget(self._build_form_panel(), stretch=1)

        self._apply_bound_state()

    # ------------------------------------------------------------------
    # Layout construction
    # ------------------------------------------------------------------

    def _build_branding_panel(self) -> QWidget:
        """Build the left-side branding panel (app name + tagline)."""
        panel = QWidget(self)
        panel.setObjectName("Sidebar")  # reuse the dark sidebar palette

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(48, 48, 48, 48)
        layout.setAlignment(Qt.AlignCenter)

        title = QLabel("نظام إدارة الحضور والانصراف", panel)
        title.setProperty("heading", "true")
        title.setStyleSheet("color: white; font-size: 22pt;")
        title.setWordWrap(True)
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Enterprise Attendance Management System", panel)
        subtitle.setStyleSheet("color: #C9CEDA; font-size: 11pt;")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        return panel

    def _build_form_panel(self) -> QWidget:
        """Build the right-side login form panel."""
        panel = QWidget(self)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(64, 64, 64, 64)
        layout.setSpacing(14)
        layout.addItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))

        heading = make_heading_label("تسجيل الدخول")
        layout.addWidget(heading)

        self.company_code_label = make_secondary_label("رمز الشركة")
        layout.addWidget(self.company_code_label)
        self.company_code_edit = QLineEdit(panel)
        self.company_code_edit.setPlaceholderText("مثال: FUTURELINE-7X4K9P")
        layout.addWidget(self.company_code_edit)

        username_label = make_secondary_label("اسم المستخدم")
        layout.addWidget(username_label)
        self.username_edit = QLineEdit(panel)
        self.username_edit.setPlaceholderText("اسم المستخدم")
        layout.addWidget(self.username_edit)

        password_label = make_secondary_label("كلمة المرور")
        layout.addWidget(password_label)
        self.password_edit = QLineEdit(panel)
        self.password_edit.setPlaceholderText("كلمة المرور")
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.returnPressed.connect(self._attempt_login)

        password_row = QHBoxLayout()
        password_row.addWidget(self.password_edit)
        self.toggle_password_button = QToolButton(panel)
        self.toggle_password_button.setText("👁")
        self.toggle_password_button.setCursor(Qt.PointingHandCursor)
        self.toggle_password_button.clicked.connect(self._toggle_password_visibility)
        password_row.addWidget(self.toggle_password_button)
        layout.addLayout(password_row)

        self.error_label = make_secondary_label("")
        self.error_label.setProperty("status", "danger")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.login_button = make_primary_button("تسجيل الدخول", parent=panel)
        self.login_button.clicked.connect(self._attempt_login)
        layout.addWidget(self.login_button)

        layout.addItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))

        return panel

    # ------------------------------------------------------------------
    # Behavior
    # ------------------------------------------------------------------

    def _apply_bound_state(self) -> None:
        """Hide the company code field once this installation is already bound.

        This application's central-server model is one device
        permanently serving one company — see :func:`_load_bound_company`.
        A never-bound installation instead shows the company code field
        (this screen never lists or offers a choice of companies).
        """
        self._bound_company = _load_bound_company()
        is_bound = self._bound_company is not None
        self.company_code_label.setVisible(not is_bound)
        self.company_code_edit.setVisible(not is_bound)

    def _toggle_password_visibility(self) -> None:
        """Flip the password field between hidden and plain-text display."""
        self._password_visible = not self._password_visible
        self.password_edit.setEchoMode(
            QLineEdit.Normal if self._password_visible else QLineEdit.Password
        )

    def _show_error(self, message: str) -> None:
        """Display an inline error message beneath the password field."""
        self.error_label.setText(message)
        self.error_label.show()

    def _clear_error(self) -> None:
        """Hide the inline error message."""
        self.error_label.clear()
        self.error_label.hide()

    def _attempt_login(self) -> None:
        """Validate input and attempt authentication, resolving a company code first if needed."""
        self._clear_error()

        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        if not username or not password:
            self._show_error("الرجاء إدخال اسم المستخدم وكلمة المرور.")
            return

        if self._bound_company is not None:
            self._authenticate_locally(self._bound_company["id"], username, password)
            return

        company_code = self.company_code_edit.text().strip()
        if not company_code:
            self._show_error("الرجاء إدخال رمز الشركة.")
            return

        self.login_button.setEnabled(False)
        try:
            resolution = self._subscription_check_service.resolve_company_code(
                company_code=company_code
            )
        finally:
            self.login_button.setEnabled(True)

        if resolution.blocked is not None:
            self._show_error(resolution.blocked.message_ar)
            return

        with session_scope() as session:
            company = CompanyService(session).company_repo.get_by_name(resolution.company_name)
        if company is None:
            self._show_error(
                "هذه الشركة غير مُعدّة على هذا الجهاز بعد. يرجى التواصل مع مسؤول النظام."
            )
            return

        self._pending_company_code = company_code
        self._authenticate_locally(company.id, username, password)

    def _authenticate_locally(self, company_id: int, username: str, password: str) -> None:
        """Attempt local username/password authentication against a resolved company."""
        self.login_button.setEnabled(False)
        try:
            controller = AuthController(
                company_id=company_id, session_manager=self.session_manager
            )
            controller.operation_failed.connect(self._on_login_failed)
            controller.login_succeeded.connect(
                lambda user: self._on_login_succeeded(user, company_id)
            )
            self._auth_controller = controller
            controller.login(username, password)
        finally:
            self.login_button.setEnabled(True)

    def _on_login_succeeded(self, user: dict[str, Any], company_id: int) -> None:
        """Confirm this installation's company binding (first login only), then relay success."""
        if self._pending_company_code is not None:
            self._subscription_check_service.confirm_local_binding(
                company_id=company_id, company_code=self._pending_company_code
            )
            self._pending_company_code = None
        self.password_edit.clear()
        self._did_succeed = True
        self.login_successful.emit(user, company_id)

    def _on_login_failed(self, message: str) -> None:
        """Surface an authentication failure inline.

        Args:
            message: The underlying error text from
                :attr:`~controllers.base_controller.BaseController.operation_failed`
                — passed through as-is (rather than replaced with a
                generic string) because
                :meth:`~services.auth_service.AuthService.login`
                deliberately distinguishes an account-lockout message
                from the generic invalid-credentials message, and both
                are meant to reach the user.
        """
        self._show_error(message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Quit the whole application if this window is closed without logging in.

        The composition root closes this window itself right after a
        successful login (see ``main.py``'s ``ApplicationController``),
        which must not be mistaken for the user closing it via the
        window manager - the ``_did_succeed`` flag distinguishes the
        two, exactly like
        :class:`~ui.subscription_blocked_window.SubscriptionBlockedWindow`'s
        ``_did_pass`` does for the same reason.
        """
        super().closeEvent(event)
        if not self._did_succeed:
            app = QApplication.instance()
            if app is not None:
                app.quit()

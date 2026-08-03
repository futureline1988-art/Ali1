"""Developer Suite login window: the application's entry screen (Phase 11).

Mirrors the Attendance Client's own ``ui/login_window.py`` structurally
(branding panel + form panel, an inline error label, a
``login_successful`` signal the composition root reacts to, "closing
without success quits the app") but authenticates against
:class:`~developer_suite.admin.session_manager.AdminSessionManager`
instead of :class:`~controllers.auth_controller.AuthController` — there
is no company picker here, since admin accounts are not scoped to a
customer's company (see :mod:`server.models.admin_account`).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from developer_suite.admin.auth_client import (
    AdminAuthAccountLockedError,
    AdminAuthClientError,
    AdminAuthConnectionError,
    AdminAuthInvalidCredentialsError,
)
from developer_suite.admin.session_manager import AdminSessionManager


class LoginWindow(QWidget):
    """The Developer Suite's entry screen.

    Emits :attr:`login_successful` once an admin account has been
    authenticated; the composition root (``developer_suite/main.py``)
    is responsible for constructing and showing the main window in
    response and closing this one.
    """

    login_successful = Signal()

    def __init__(self, session_manager: AdminSessionManager, *, parent: QWidget | None = None) -> None:
        """Build the login screen.

        Args:
            session_manager: Authenticates the entered credentials and
                owns the resulting session (see
                :mod:`developer_suite.admin.session_manager`).
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("تسجيل الدخول - مجموعة أدوات المطورين")
        self.setMinimumSize(820, 480)

        self._session_manager = session_manager
        self._password_visible = False
        self._did_succeed = False

        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_branding_panel(), stretch=1)
        root_layout.addWidget(self._build_form_panel(), stretch=1)

    # ------------------------------------------------------------------
    # Layout construction
    # ------------------------------------------------------------------

    def _build_branding_panel(self) -> QWidget:
        """Build the left-side branding panel."""
        panel = QWidget(self)
        panel.setStyleSheet("background-color: #1F2937;")

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(48, 48, 48, 48)
        layout.setAlignment(Qt.AlignCenter)

        title = QLabel("مجموعة أدوات المطورين", panel)
        title.setStyleSheet("color: white; font-size: 20pt; font-weight: bold;")
        title.setWordWrap(True)
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("إدارة العملاء والتراخيص والمزامنة", panel)
        subtitle.setStyleSheet("color: #C9CEDA; font-size: 11pt;")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        return panel

    def _build_form_panel(self) -> QWidget:
        """Build the right-side login form panel."""
        panel = QWidget(self)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(56, 56, 56, 56)
        layout.setSpacing(12)
        layout.addItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))

        heading = QLabel("تسجيل الدخول", panel)
        heading.setStyleSheet("font-size: 16pt; font-weight: bold;")
        layout.addWidget(heading)

        username_label = QLabel("اسم المستخدم", panel)
        layout.addWidget(username_label)
        self.username_edit = QLineEdit(panel)
        self.username_edit.setPlaceholderText("اسم المستخدم")
        layout.addWidget(self.username_edit)

        password_label = QLabel("كلمة المرور", panel)
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

        self.remember_me_checkbox = QCheckBox("تذكرني على هذا الجهاز", panel)
        layout.addWidget(self.remember_me_checkbox)

        self.error_label = QLabel("", panel)
        self.error_label.setStyleSheet("color: #DC2626;")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.login_button = QPushButton("تسجيل الدخول", panel)
        self.login_button.setCursor(Qt.PointingHandCursor)
        self.login_button.clicked.connect(self._attempt_login)
        layout.addWidget(self.login_button)

        layout.addItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))

        return panel

    # ------------------------------------------------------------------
    # Behavior
    # ------------------------------------------------------------------

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
        """Validate input and attempt authentication."""
        self._clear_error()

        username = self.username_edit.text().strip()
        password = self.password_edit.text()

        if not username or not password:
            self._show_error("الرجاء إدخال اسم المستخدم وكلمة المرور.")
            return

        self.login_button.setEnabled(False)
        try:
            self._session_manager.login(
                username, password, remember_me=self.remember_me_checkbox.isChecked()
            )
        except AdminAuthAccountLockedError:
            self._show_error("هذا الحساب مقفل مؤقتًا بسبب محاولات دخول فاشلة متكررة.")
        except AdminAuthInvalidCredentialsError:
            self._show_error("اسم المستخدم أو كلمة المرور غير صحيحة.")
        except AdminAuthConnectionError:
            self._show_error("تعذر الاتصال بخادم الحضور. الرجاء التحقق من الاتصال والمحاولة مرة أخرى.")
        except AdminAuthClientError as exc:
            self._show_error(str(exc))
        else:
            self.password_edit.clear()
            self._did_succeed = True
            self.login_successful.emit()
        finally:
            self.login_button.setEnabled(True)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Quit the whole application if this window is closed without logging in.

        Mirrors :meth:`ui.login_window.LoginWindow.closeEvent`'s own
        ``_did_succeed`` guard, for the same reason: the composition
        root closes this window itself right after a successful login,
        which must not be mistaken for the user closing it via the
        window manager.
        """
        super().closeEvent(event)
        if not self._did_succeed:
            app = QApplication.instance()
            if app is not None:
                app.quit()

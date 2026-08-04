"""Developer Suite first-run setup wizard: creates the very first admin account.

Shown by ``developer_suite/main.py`` instead of
:class:`~developer_suite.ui.login_window.LoginWindow` when
:meth:`~developer_suite.admin.session_manager.AdminSessionManager.needs_initial_setup`
reports that the Attendance Server has no admin account yet — the
interactive replacement for the environment-variable bootstrap seeding
Phase 11 originally shipped with (see
``server/database/bootstrap.py``'s module docstring for why that was
removed). Mirrors :class:`~developer_suite.ui.login_window.LoginWindow`'s
shape closely (branding panel + form panel, an inline error label, a
success signal the composition root reacts to, "closing without
success quits the app") since this is functionally the same "entry
screen before the main window exists" role, just for the one-time case
where there is no account to log into yet.
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
    AdminAuthClientError,
    AdminAuthConnectionError,
    AdminAuthPasswordPolicyError,
    AdminAuthSetupAlreadyCompletedError,
)
from developer_suite.admin.session_manager import AdminSessionManager


class FirstRunSetupWindow(QWidget):
    """The Developer Suite's one-time "create the first administrator" screen.

    Emits :attr:`setup_successful` once the account has been created
    and a session started for it; the composition root
    (``developer_suite/main.py``) is responsible for constructing and
    showing the main window in response and closing this one — exactly
    like :attr:`~developer_suite.ui.login_window.LoginWindow.login_successful`.
    """

    setup_successful = Signal()

    def __init__(self, session_manager: AdminSessionManager, *, parent: QWidget | None = None) -> None:
        """Build the first-run setup screen.

        Args:
            session_manager: Creates the account and owns the
                resulting session (see
                :mod:`developer_suite.admin.session_manager`).
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("الإعداد الأول - مجموعة أدوات المطورين")
        self.setMinimumSize(820, 560)

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

        subtitle = QLabel("مرحبًا بك! لم يتم إعداد أي حساب مدير بعد.", panel)
        subtitle.setStyleSheet("color: #C9CEDA; font-size: 11pt;")
        subtitle.setWordWrap(True)
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        return panel

    def _build_form_panel(self) -> QWidget:
        """Build the right-side setup form panel."""
        panel = QWidget(self)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(56, 40, 56, 40)
        layout.setSpacing(10)
        layout.addItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))

        heading = QLabel("إنشاء حساب المدير الأول", panel)
        heading.setStyleSheet("font-size: 16pt; font-weight: bold;")
        layout.addWidget(heading)

        description = QLabel(
            "لم يتم العثور على أي حساب مدير على خادم الحضور. أنشئ الحساب الأول "
            "هنا؛ ستتمكن بعدها من إنشاء العملاء وتوليد التراخيص وتفعيل أجهزة "
            "العملاء. لن يظهر هذا المعالج مرة أخرى بعد إنشاء هذا الحساب.",
            panel,
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: #6B7280;")
        layout.addWidget(description)

        username_label = QLabel("اسم المستخدم", panel)
        layout.addWidget(username_label)
        self.username_edit = QLineEdit(panel)
        self.username_edit.setPlaceholderText("اسم المستخدم")
        layout.addWidget(self.username_edit)

        full_name_label = QLabel("الاسم الكامل (اختياري)", panel)
        layout.addWidget(full_name_label)
        self.full_name_edit = QLineEdit(panel)
        self.full_name_edit.setPlaceholderText("الاسم الكامل")
        layout.addWidget(self.full_name_edit)

        password_label = QLabel("كلمة المرور", panel)
        layout.addWidget(password_label)
        self.password_edit = QLineEdit(panel)
        self.password_edit.setPlaceholderText("كلمة المرور")
        self.password_edit.setEchoMode(QLineEdit.Password)

        password_row = QHBoxLayout()
        password_row.addWidget(self.password_edit)
        self.toggle_password_button = QToolButton(panel)
        self.toggle_password_button.setText("👁")
        self.toggle_password_button.setCursor(Qt.PointingHandCursor)
        self.toggle_password_button.clicked.connect(self._toggle_password_visibility)
        password_row.addWidget(self.toggle_password_button)
        layout.addLayout(password_row)

        password_hint = QLabel("يجب أن تحتوي كلمة المرور على حرف ورقم واحد على الأقل.", panel)
        password_hint.setStyleSheet("color: #6B7280; font-size: 9pt;")
        password_hint.setWordWrap(True)
        layout.addWidget(password_hint)

        confirm_label = QLabel("تأكيد كلمة المرور", panel)
        layout.addWidget(confirm_label)
        self.confirm_password_edit = QLineEdit(panel)
        self.confirm_password_edit.setPlaceholderText("تأكيد كلمة المرور")
        self.confirm_password_edit.setEchoMode(QLineEdit.Password)
        self.confirm_password_edit.returnPressed.connect(self._attempt_setup)
        layout.addWidget(self.confirm_password_edit)

        self.remember_me_checkbox = QCheckBox("تذكرني على هذا الجهاز", panel)
        layout.addWidget(self.remember_me_checkbox)

        self.error_label = QLabel("", panel)
        self.error_label.setStyleSheet("color: #DC2626;")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.setup_button = QPushButton("إنشاء الحساب", panel)
        self.setup_button.setCursor(Qt.PointingHandCursor)
        self.setup_button.clicked.connect(self._attempt_setup)
        layout.addWidget(self.setup_button)

        layout.addItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))

        return panel

    # ------------------------------------------------------------------
    # Behavior
    # ------------------------------------------------------------------

    def _toggle_password_visibility(self) -> None:
        """Flip both password fields between hidden and plain-text display.

        Both fields toggle together (unlike a typical single-field
        login form) since the whole point of the confirmation field
        here is to catch a typo — seeing both at once is more useful
        than seeing only one.
        """
        self._password_visible = not self._password_visible
        mode = QLineEdit.Normal if self._password_visible else QLineEdit.Password
        self.password_edit.setEchoMode(mode)
        self.confirm_password_edit.setEchoMode(mode)

    def _show_error(self, message: str) -> None:
        """Display an inline error message beneath the form fields."""
        self.error_label.setText(message)
        self.error_label.show()

    def _clear_error(self) -> None:
        """Hide the inline error message."""
        self.error_label.clear()
        self.error_label.hide()

    def _attempt_setup(self) -> None:
        """Validate input and attempt to create the first admin account."""
        self._clear_error()

        username = self.username_edit.text().strip()
        full_name = self.full_name_edit.text().strip() or None
        password = self.password_edit.text()
        confirm_password = self.confirm_password_edit.text()

        if not username or not password:
            self._show_error("الرجاء إدخال اسم المستخدم وكلمة المرور.")
            return
        if password != confirm_password:
            self._show_error("كلمتا المرور غير متطابقتين.")
            return

        self.setup_button.setEnabled(False)
        try:
            self._session_manager.complete_first_run_setup(
                username,
                password,
                full_name=full_name,
                remember_me=self.remember_me_checkbox.isChecked(),
            )
        except AdminAuthSetupAlreadyCompletedError:
            self._show_error(
                "تم إنشاء حساب مدير بالفعل من جهاز آخر. الرجاء إغلاق هذه النافذة "
                "وإعادة تشغيل التطبيق لتسجيل الدخول."
            )
        except AdminAuthPasswordPolicyError:
            self._show_error("كلمة المرور لا تفي بمتطلبات القوة المطلوبة.")
        except AdminAuthConnectionError:
            self._show_error("تعذر الاتصال بخادم الحضور. الرجاء التحقق من الاتصال والمحاولة مرة أخرى.")
        except AdminAuthClientError as exc:
            self._show_error(str(exc))
        else:
            self.password_edit.clear()
            self.confirm_password_edit.clear()
            self._did_succeed = True
            self.setup_successful.emit()
        finally:
            self.setup_button.setEnabled(True)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Quit the whole application if this window is closed without completing setup.

        Mirrors :meth:`~developer_suite.ui.login_window.LoginWindow.closeEvent`'s
        own ``_did_succeed`` guard, for the same reason: the
        composition root closes this window itself right after a
        successful setup, which must not be mistaken for the user
        closing it via the window manager.
        """
        super().closeEvent(event)
        if not self._did_succeed:
            app = QApplication.instance()
            if app is not None:
                app.quit()

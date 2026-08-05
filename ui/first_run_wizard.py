"""First-run setup wizard: the only way a brand-new installation gets its first company.

Shown by ``main.py`` exactly once — the moment a fresh install has no
local company yet (see :func:`main._has_any_company`). Walks the
customer through everything needed to start using the software
immediately and fully offline: company name/logo, brand colors,
creating the administrator account, and (optionally) adding and
testing the first attendance device. Nothing here ever talks to a
network — every step is a local database write through the same
services every other screen uses (:class:`~services.company_service.CompanyService`,
:class:`~services.user_service.UserService`,
:class:`~services.device_service.DeviceService`).

On completion, this wizard authenticates the freshly-created
administrator through the normal :class:`~controllers.auth_controller.AuthController`
login path (rather than hand-building a session) and emits
:attr:`FirstRunWizard.setup_completed` with the exact same
``(user_dict, company_id)`` shape :class:`~ui.login_window.LoginWindow`'s
``login_successful`` emits, so ``main.py``'s ``ApplicationController``
handles both identically.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from config import get_config
from controllers.auth_controller import AuthController
from database.database import session_scope
from devices.device_manager import DeviceManager
from models.device import Device
from models.enums import DeviceProtocol, UserRole
from repositories.company_settings_repository import CompanySettingsRepository
from repositories.role_repository import RoleRepository
from services.company_service import CompanyService, CompanyValidationError
from services.device_service import DeviceService, DeviceValidationError
from services.user_service import UserManagementError, UserService
from ui.devices import _PROTOCOL_LABELS_AR
from ui.widgets import make_primary_button, make_secondary_label, run_blocking_operation
from utils.security import SessionManager

_DEFAULT_BRAND_PRIMARY = "#2563EB"
_DEFAULT_BRAND_SECONDARY = "#0F172A"
_DEFAULT_BRAND_ACCENT = "#F59E0B"


def _color_swatch_button(initial_hex: str, parent: QWidget) -> QPushButton:
    """Create a small button that shows/picks a hex color."""
    button = QPushButton(initial_hex, parent)
    button.setProperty("hex_color", initial_hex)
    _apply_swatch_style(button, initial_hex)
    return button


def _apply_swatch_style(button: QPushButton, hex_color: str) -> None:
    """Paint ``button`` with ``hex_color`` as its background."""
    button.setStyleSheet(f"background-color: {hex_color}; color: white; padding: 6px;")
    button.setProperty("hex_color", hex_color)


class _CompanyPage(QWizardPage):
    """Page 1: company name and logo."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("معلومات الشركة")
        self.setSubTitle("أدخل اسم شركتك وشعارها (اختياري).")

        self._logo_path: str | None = None

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(12)

        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText("اسم الشركة")
        form.addRow("اسم الشركة *", self.name_edit)
        self.registerField("company_name*", self.name_edit)

        logo_row = QHBoxLayout()
        self.logo_label = make_secondary_label("لم يتم اختيار شعار")
        logo_row.addWidget(self.logo_label, stretch=1)
        browse_button = make_primary_button("استعراض...", parent=self)
        browse_button.clicked.connect(self._on_browse_logo_clicked)
        logo_row.addWidget(browse_button)
        form.addRow("شعار الشركة", logo_row)

        layout.addLayout(form)
        layout.addStretch(1)

    def _on_browse_logo_clicked(self) -> None:
        chosen, _filter = QFileDialog.getOpenFileName(
            self, "اختيار شعار الشركة", "", "Images (*.png *.jpg *.jpeg *.svg)"
        )
        if chosen:
            self._logo_path = chosen
            self.logo_label.setText(Path(chosen).name)

    @property
    def logo_path(self) -> str | None:
        return self._logo_path


class _BrandingPage(QWizardPage):
    """Page 2: brand colors."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("الهوية البصرية")
        self.setSubTitle("اختر ألوان العلامة التجارية لشركتك.")

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(12)

        self.primary_button = _color_swatch_button(_DEFAULT_BRAND_PRIMARY, self)
        self.primary_button.clicked.connect(lambda: self._pick_color(self.primary_button))
        form.addRow("اللون الأساسي", self.primary_button)

        self.secondary_button = _color_swatch_button(_DEFAULT_BRAND_SECONDARY, self)
        self.secondary_button.clicked.connect(lambda: self._pick_color(self.secondary_button))
        form.addRow("اللون الثانوي", self.secondary_button)

        self.accent_button = _color_swatch_button(_DEFAULT_BRAND_ACCENT, self)
        self.accent_button.clicked.connect(lambda: self._pick_color(self.accent_button))
        form.addRow("لون التمييز", self.accent_button)

        layout.addLayout(form)
        layout.addStretch(1)

    def _pick_color(self, button: QPushButton) -> None:
        current = QColor(button.property("hex_color"))
        color = QColorDialog.getColor(current, self, "اختيار لون")
        if color.isValid():
            _apply_swatch_style(button, color.name())

    @property
    def primary_color(self) -> str:
        return self.primary_button.property("hex_color")

    @property
    def secondary_color(self) -> str:
        return self.secondary_button.property("hex_color")

    @property
    def accent_color(self) -> str:
        return self.accent_button.property("hex_color")


class _AdminAccountPage(QWizardPage):
    """Page 3: create the administrator account."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("حساب المسؤول")
        self.setSubTitle("أنشئ حساب مسؤول النظام الأول لهذه الشركة.")

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(12)

        self.full_name_edit = QLineEdit(self)
        form.addRow("الاسم الكامل *", self.full_name_edit)
        self.registerField("admin_full_name*", self.full_name_edit)

        self.username_edit = QLineEdit(self)
        form.addRow("اسم المستخدم *", self.username_edit)
        self.registerField("admin_username*", self.username_edit)

        self.password_edit = QLineEdit(self)
        self.password_edit.setEchoMode(QLineEdit.Password)
        form.addRow("كلمة المرور *", self.password_edit)
        self.registerField("admin_password*", self.password_edit)

        self.confirm_password_edit = QLineEdit(self)
        self.confirm_password_edit.setEchoMode(QLineEdit.Password)
        form.addRow("تأكيد كلمة المرور *", self.confirm_password_edit)
        self.registerField("admin_password_confirm*", self.confirm_password_edit)

        layout.addLayout(form)
        self.error_label = make_secondary_label("")
        self.error_label.setProperty("status", "danger")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        layout.addStretch(1)

    def validatePage(self) -> bool:
        self.error_label.hide()
        if self.password_edit.text() != self.confirm_password_edit.text():
            self.error_label.setText("كلمتا المرور غير متطابقتين.")
            self.error_label.show()
            return False
        return True


class _DevicePage(QWizardPage):
    """Page 4: optionally add and test the first attendance device."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("جهاز البصمة")
        self.setSubTitle(
            "أضف جهاز حضور وانصراف الآن، أو تخطَّ هذه الخطوة وأضفه لاحقاً من شاشة الأجهزة."
        )
        self._last_test_succeeded = False

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(12)

        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText("مثال: جهاز البوابة الرئيسية")
        form.addRow("اسم الجهاز", self.name_edit)

        self.protocol_combo = QComboBox(self)
        for protocol, label in _PROTOCOL_LABELS_AR.items():
            self.protocol_combo.addItem(label, userData=protocol)
        form.addRow("البروتوكول", self.protocol_combo)

        self.host_edit = QLineEdit(self)
        self.host_edit.setPlaceholderText("192.168.1.201")
        form.addRow("عنوان الجهاز (IP)", self.host_edit)

        host_hint = make_secondary_label(
            "يظهر هذا العنوان على شاشة جهاز البصمة نفسه: القائمة ← الاتصال ← الشبكة/Ethernet."
        )
        host_hint.setWordWrap(True)
        form.addRow("", host_hint)

        self.port_spin = QSpinBox(self)
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(4370)
        form.addRow("المنفذ", self.port_spin)

        self.communication_key_edit = QLineEdit(self)
        self.communication_key_edit.setPlaceholderText("كلمة اتصال ZKTeco (إن وجدت)")
        form.addRow("مفتاح الاتصال", self.communication_key_edit)

        layout.addLayout(form)

        actions_row = QHBoxLayout()
        self.test_button = make_primary_button("اختبار الاتصال", parent=self)
        self.test_button.clicked.connect(self._on_test_clicked)
        actions_row.addWidget(self.test_button)
        actions_row.addStretch(1)
        layout.addLayout(actions_row)

        self.result_label = make_secondary_label("")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        layout.addStretch(1)

    def _on_test_clicked(self) -> None:
        host = self.host_edit.text().strip()
        if not host:
            self.result_label.setProperty("status", "danger")
            self.result_label.setText("الرجاء إدخال عنوان الجهاز أولاً.")
            self._repolish_result_label()
            return

        probe = Device(
            name=self.name_edit.text().strip() or "probe",
            protocol=self.protocol_combo.currentData(),
            host=host,
            port=self.port_spin.value(),
            communication_key=self.communication_key_edit.text().strip() or None,
        )
        connector = DeviceManager().get_connector(probe)
        try:
            result = run_blocking_operation(
                self, "جارٍ اختبار الاتصال بالجهاز...", connector.test_connection_detailed
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the user as-is
            result = None
            self.result_label.setText(f"فشل الاتصال: {exc}")
        else:
            self.result_label.setText(result.message_ar)
        finally:
            connector.disconnect()

        self._last_test_succeeded = bool(result and result.success)
        self.result_label.setProperty("status", "success" if self._last_test_succeeded else "danger")
        self._repolish_result_label()

    def _repolish_result_label(self) -> None:
        style = self.result_label.style()
        style.unpolish(self.result_label)
        style.polish(self.result_label)

    def has_device(self) -> bool:
        return bool(self.host_edit.text().strip())

    def device_values(self) -> dict[str, Any]:
        return {
            "name": self.name_edit.text().strip() or "الجهاز الرئيسي",
            "protocol": DeviceProtocol(self.protocol_combo.currentData()),
            "host": self.host_edit.text().strip(),
            "port": self.port_spin.value(),
            "communication_key": self.communication_key_edit.text().strip() or None,
        }


class _SummaryPage(QWizardPage):
    """Page 5: confirm and finish."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("جاهز للبدء")
        self.setSubTitle("راجع المعلومات ثم اضغط \"إنهاء\" لبدء استخدام النظام.")

        layout = QVBoxLayout(self)
        self.summary_label = QLabel(self)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)
        layout.addStretch(1)

    def initializePage(self) -> None:
        wizard = self.wizard()
        assert isinstance(wizard, FirstRunWizard)
        lines = [
            f"الشركة: {wizard.company_page.name_edit.text().strip()}",
            f"المسؤول: {wizard.admin_page.full_name_edit.text().strip()} "
            f"({wizard.admin_page.username_edit.text().strip()})",
        ]
        if wizard.device_page.has_device():
            lines.append(f"الجهاز: {wizard.device_page.name_edit.text().strip()}")
        self.summary_label.setText("\n".join(lines))


class FirstRunWizard(QWizard):
    """The complete first-run setup flow for a brand-new installation."""

    setup_completed = Signal(dict, int)
    """Emitted with ``(user_dict, company_id)`` once setup finishes and the new administrator is logged in."""

    def __init__(
        self, *, session_manager: SessionManager | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("إعداد النظام - نظام إدارة الحضور والانصراف")
        self.setMinimumSize(720, 560)
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.NoBackButtonOnLastPage, False)

        self.session_manager = session_manager or SessionManager()
        self._auth_controller: AuthController | None = None
        self._did_finish = False

        self.company_page = _CompanyPage(self)
        self.branding_page = _BrandingPage(self)
        self.admin_page = _AdminAccountPage(self)
        self.device_page = _DevicePage(self)
        self.summary_page = _SummaryPage(self)

        self.addPage(self.company_page)
        self.addPage(self.branding_page)
        self.addPage(self.admin_page)
        self.addPage(self.device_page)
        self.addPage(self.summary_page)

        self.setButtonText(QWizard.FinishButton, "إنهاء")
        self.setButtonText(QWizard.NextButton, "التالي")
        self.setButtonText(QWizard.BackButton, "السابق")
        self.setButtonText(QWizard.CancelButton, "إلغاء")

        self.accepted.connect(self._on_finished)

    def _copy_logo(self, source_path: str) -> str | None:
        """Copy the chosen logo into this installation's own uploads directory.

        Keeps the stored path valid even if the original file the user
        picked is later moved or deleted.
        """
        source = Path(source_path)
        if not source.is_file():
            return None
        dest_dir = get_config().paths.uploads_dir / "branding"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"logo{source.suffix.lower()}"
        shutil.copyfile(source, dest)
        return str(dest)

    def _on_finished(self) -> None:
        """Create the company, branding, administrator, and optional device, then log in."""
        company_name = self.company_page.name_edit.text().strip()
        logo_source = self.company_page.logo_path
        username = self.admin_page.username_edit.text().strip()
        full_name = self.admin_page.full_name_edit.text().strip()
        password = self.admin_page.password_edit.text()

        try:
            with session_scope() as session:
                company_service = CompanyService(session)
                company = company_service.create_company(name=company_name)
                company_id = company.id

                if logo_source:
                    logo_dest = self._copy_logo(logo_source)
                    if logo_dest:
                        company_service.update_company_info(company, logo_path=logo_dest)

                settings_repo = CompanySettingsRepository(session, company_id=company_id)
                settings = settings_repo.get_or_create()
                settings.theme_primary_color = self.branding_page.primary_color
                settings.theme_secondary_color = self.branding_page.secondary_color
                settings.theme_accent_color = self.branding_page.accent_color
                session.flush()

                role = RoleRepository(session, company_id=company_id).get_by_code(
                    UserRole.SYSTEM_ADMIN.value
                )
                UserService(session, company_id=company_id).create_user(
                    username=username, full_name=full_name, password=password, role_id=role.id
                )

                if self.device_page.has_device():
                    DeviceService(session, company_id=company_id).create_device(
                        **self.device_page.device_values()
                    )
        except (CompanyValidationError, UserManagementError, DeviceValidationError) as exc:
            QMessageBox.critical(self, "تعذر إكمال الإعداد", str(exc))
            return

        controller = AuthController(company_id=company_id, session_manager=self.session_manager)
        controller.login_succeeded.connect(
            lambda user: self._on_admin_logged_in(user, company_id)
        )
        controller.operation_failed.connect(self._on_login_after_setup_failed)
        self._auth_controller = controller
        controller.login(username, password)

    def _on_admin_logged_in(self, user: dict[str, Any], company_id: int) -> None:
        self._did_finish = True
        self.setup_completed.emit(user, company_id)

    def _on_login_after_setup_failed(self, message: str) -> None:
        QMessageBox.critical(
            self,
            "تعذر تسجيل الدخول",
            f"تم إنشاء الحساب لكن تعذر تسجيل الدخول تلقائياً: {message}\n"
            "الرجاء إعادة تشغيل التطبيق وتسجيل الدخول يدوياً.",
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Quit the whole application if this wizard is closed before finishing."""
        super().closeEvent(event)
        if not self._did_finish:
            app = QApplication.instance()
            if app is not None:
                app.quit()

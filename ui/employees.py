"""Employees screen: list, search, and manage employee records."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from controllers.branch_controller import BranchController
from controllers.department_controller import DepartmentController
from controllers.device_controller import DeviceController
from controllers.employee_controller import EmployeeController
from controllers.payroll_controller import PayrollController
from devices.deli_es172_device import (
    ENROLLMENT_KIND_FACE,
    ENROLLMENT_KIND_FINGERPRINT,
    ENROLLMENT_KIND_PALM,
)
from models.enums import DeviceProtocol, PayrollAdjustmentType
from ui.deli_enrollment_dialog import DeliEnrollmentDialog
from ui.face_enrollment_dialog import BiometricStatusDialog, FaceEnrollmentDialog, SelectDeviceDialog
from ui.payroll import PayrollAdjustmentFormDialog
from ui.table_page import TablePage
from ui.widgets import ConfirmDialog, make_danger_button, run_blocking_operation


class EmployeeFormDialog(QDialog):
    """Add/edit form for a single employee.

    Construct with ``existing=None`` for "add" mode, or an employee
    dict (as returned by
    :class:`~controllers.employee_controller.EmployeeController`) for
    "edit" mode, which pre-fills every field.
    """

    def __init__(
        self,
        *,
        departments: list[dict[str, Any]],
        branches: list[dict[str, Any]] | None = None,
        existing: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Build the form dialog.

        Args:
            departments: Available departments (``{"id", "name", ...}``
                dicts) to populate the department picker.
            branches: Available branches (``{"id", "name", ...}`` dicts)
                to populate the branch picker; omit or pass an empty
                list if this company has none defined yet.
            existing: The employee being edited, or ``None`` to create
                a new one.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._existing = existing
        self.setWindowTitle("تعديل بيانات موظف" if existing else "إضافة موظف جديد")
        self.setMinimumWidth(420)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.employee_number_edit = QLineEdit(self)
        form.addRow("الرقم الوظيفي *", self.employee_number_edit)

        self.full_name_edit = QLineEdit(self)
        form.addRow("الاسم الكامل *", self.full_name_edit)

        self.department_combo = QComboBox(self)
        self.department_combo.addItem("بدون قسم", userData=None)
        for department in departments:
            self.department_combo.addItem(department["name"], userData=department["id"])
        form.addRow("القسم", self.department_combo)

        self.branch_combo = QComboBox(self)
        self.branch_combo.addItem("بدون فرع", userData=None)
        for branch in branches or []:
            self.branch_combo.addItem(branch["name"], userData=branch["id"])
        form.addRow("الفرع", self.branch_combo)

        self.national_id_edit = QLineEdit(self)
        form.addRow("الرقم الوطني", self.national_id_edit)

        self.email_edit = QLineEdit(self)
        form.addRow("البريد الإلكتروني", self.email_edit)

        self.phone_edit = QLineEdit(self)
        form.addRow("الهاتف", self.phone_edit)

        self.position_edit = QLineEdit(self)
        form.addRow("المسمى الوظيفي", self.position_edit)

        self.salary_spin = QDoubleSpinBox(self)
        self.salary_spin.setRange(0, 999_999_999)
        self.salary_spin.setDecimals(2)
        self.salary_spin.setSuffix(" د.ع")
        form.addRow("الراتب", self.salary_spin)

        self.hire_date_edit = QDateEdit(self)
        self.hire_date_edit.setCalendarPopup(True)
        self.hire_date_edit.setDate(QDate.currentDate())
        form.addRow("تاريخ التعيين", self.hire_date_edit)

        self.notes_edit = QPlainTextEdit(self)
        self.notes_edit.setFixedHeight(70)
        form.addRow("ملاحظات", self.notes_edit)

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
        """Pre-fill every field from an existing employee's data."""
        self.employee_number_edit.setText(existing.get("employee_number") or "")
        self.full_name_edit.setText(existing.get("full_name") or "")

        department_id = existing.get("department_id")
        index = self.department_combo.findData(department_id)
        self.department_combo.setCurrentIndex(index if index >= 0 else 0)

        branch_id = existing.get("branch_id")
        branch_index = self.branch_combo.findData(branch_id)
        self.branch_combo.setCurrentIndex(branch_index if branch_index >= 0 else 0)

        self.national_id_edit.setText(existing.get("national_id") or "")
        self.email_edit.setText(existing.get("email") or "")
        self.phone_edit.setText(existing.get("phone") or "")
        self.position_edit.setText(existing.get("position") or "")

        salary = existing.get("salary")
        if salary is not None:
            self.salary_spin.setValue(float(salary))

        hire_date_value = existing.get("hire_date")
        if hire_date_value:
            parsed = date.fromisoformat(hire_date_value)
            self.hire_date_edit.setDate(QDate(parsed.year, parsed.month, parsed.day))

        self.notes_edit.setPlainText(existing.get("notes") or "")

    def values(self) -> dict[str, Any]:
        """Read the form's current state as keyword arguments for the controller.

        Returns:
            A dict compatible with
            :meth:`~controllers.employee_controller.EmployeeController.create_employee`
            / ``update_employee``.
        """
        salary_value = self.salary_spin.value()
        try:
            salary = Decimal(str(salary_value)) if salary_value > 0 else None
        except InvalidOperation:
            salary = None

        qdate = self.hire_date_edit.date()
        hire_date = date(qdate.year(), qdate.month(), qdate.day())

        return {
            "employee_number": self.employee_number_edit.text().strip(),
            "full_name": self.full_name_edit.text().strip(),
            "department_id": self.department_combo.currentData(),
            "branch_id": self.branch_combo.currentData(),
            "national_id": self.national_id_edit.text().strip() or None,
            "email": self.email_edit.text().strip() or None,
            "phone": self.phone_edit.text().strip() or None,
            "position": self.position_edit.text().strip() or None,
            "salary": salary,
            "hire_date": hire_date,
            "notes": self.notes_edit.toPlainText().strip() or None,
        }


class EmployeesPage(TablePage):
    """The employees management screen."""

    def __init__(
        self,
        *,
        company_id: int,
        current_user_id: int | None = None,
        permission_codes: frozenset[str] = frozenset(),
        parent: QWidget | None = None,
    ) -> None:
        """Create the employees page.

        Args:
            company_id: The company this screen manages employees for.
            current_user_id: The signed-in user, for audit attribution.
            permission_codes: The signed-in user's granted permission
                codes.
            parent: Optional parent widget.
        """
        super().__init__(
            title="الموظفون",
            add_button_text="+ إضافة موظف",
            search_placeholder="بحث بالاسم...",
            parent=parent,
        )
        self._company_id = company_id
        self._controller = EmployeeController(
            company_id=company_id,
            actor_user_id=current_user_id,
            permission_codes=permission_codes,
        )
        self._controller.operation_failed.connect(self.show_error)
        self._department_controller = DepartmentController(
            company_id=company_id,
            actor_user_id=current_user_id,
            permission_codes=permission_codes,
        )
        self._branch_controller = BranchController(
            company_id=company_id,
            actor_user_id=current_user_id,
            permission_codes=permission_codes,
        )
        self._device_controller = DeviceController(
            company_id=company_id,
            actor_user_id=current_user_id,
            permission_codes=permission_codes,
        )
        self._device_controller.operation_failed.connect(self.show_error)
        self._payroll_controller = PayrollController(
            company_id=company_id,
            actor_user_id=current_user_id,
            permission_codes=permission_codes,
        )
        self._payroll_controller.operation_failed.connect(self.show_error)

        self.push_to_device_button = QPushButton("إرسال إلى جهاز البصمة", self)
        self.push_to_device_button.setEnabled(False)
        self.push_to_device_button.clicked.connect(self._on_push_to_device_clicked)
        self.toolbar_layout.addWidget(self.push_to_device_button)

        self.register_face_button = QPushButton("تسجيل بصمة الوجه", self)
        self.register_face_button.setEnabled(False)
        self.register_face_button.clicked.connect(self._on_register_face_clicked)
        self.toolbar_layout.addWidget(self.register_face_button)

        self.biometric_status_button = QPushButton("الحالة البيومترية", self)
        self.biometric_status_button.setEnabled(False)
        self.biometric_status_button.clicked.connect(self._on_biometric_status_clicked)
        self.toolbar_layout.addWidget(self.biometric_status_button)

        self.register_fingerprint_deli_button = QPushButton("تسجيل بصمة الإصبع (DELI)", self)
        self.register_fingerprint_deli_button.setEnabled(False)
        self.register_fingerprint_deli_button.clicked.connect(
            self._on_register_fingerprint_deli_clicked
        )
        self.toolbar_layout.addWidget(self.register_fingerprint_deli_button)

        self.register_palm_deli_button = QPushButton("تسجيل بصمة الكف (DELI)", self)
        self.register_palm_deli_button.setEnabled(False)
        self.register_palm_deli_button.clicked.connect(self._on_register_palm_deli_clicked)
        self.toolbar_layout.addWidget(self.register_palm_deli_button)

        self.add_deduction_button = QPushButton("إضافة خصم", self)
        self.add_deduction_button.setEnabled(False)
        self.add_deduction_button.clicked.connect(self._on_add_deduction_clicked)
        self.toolbar_layout.addWidget(self.add_deduction_button)

        self.add_bonus_button = QPushButton("إضافة مكافأة", self)
        self.add_bonus_button.setEnabled(False)
        self.add_bonus_button.clicked.connect(self._on_add_bonus_clicked)
        self.toolbar_layout.addWidget(self.add_bonus_button)

        self.delete_button = make_danger_button("حذف", parent=self)
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self._on_delete_clicked)
        self.toolbar_layout.addWidget(self.delete_button)

        self.set_columns(
            [
                ("employee_number", "الرقم الوظيفي", lambda row: row.get("employee_number") or ""),
                ("full_name", "الاسم الكامل", lambda row: row.get("full_name") or ""),
                ("department_name", "القسم", lambda row: row.get("department_name") or "—"),
                ("position", "المسمى الوظيفي", lambda row: row.get("position") or "—"),
                ("phone", "الهاتف", lambda row: row.get("phone") or "—"),
                (
                    "employment_status_label_ar",
                    "الحالة",
                    lambda row: row.get("employment_status_label_ar") or "",
                ),
            ]
        )

        self.add_requested.connect(self._on_add_clicked)
        self.row_activated.connect(self._on_edit_row)
        self.search_changed.connect(self._on_search_changed)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        self.refresh()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Reload the employee list from the database."""
        self.clear_error()
        rows = self._controller.list_employees()
        self.populate(rows)
        self._on_selection_changed()

    def _on_search_changed(self, query: str) -> None:
        """Reload the table filtered by ``query`` (or all employees if empty)."""
        self.clear_error()
        rows = (
            self._controller.search_employees(query)
            if query.strip()
            else self._controller.list_employees()
        )
        self.populate(rows)
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        """Enable/disable selection-dependent buttons based on the current selection."""
        has_selection = self.selected_row() is not None
        self.delete_button.setEnabled(has_selection)
        self.push_to_device_button.setEnabled(has_selection)
        self.register_face_button.setEnabled(has_selection)
        self.biometric_status_button.setEnabled(has_selection)
        self.register_fingerprint_deli_button.setEnabled(has_selection)
        self.register_palm_deli_button.setEnabled(has_selection)
        self.add_deduction_button.setEnabled(has_selection)
        self.add_bonus_button.setEnabled(has_selection)

    # ------------------------------------------------------------------
    # Add / edit / delete
    # ------------------------------------------------------------------

    def _load_department_choices(self) -> list[dict[str, Any]]:
        """Fetch every department for the form's department picker."""
        return self._department_controller.list_all()

    def _load_branch_choices(self) -> list[dict[str, Any]]:
        """Fetch every branch for the form's branch picker."""
        return self._branch_controller.list_branches()

    def _on_add_clicked(self) -> None:
        """Open the "add employee" dialog and persist the result if accepted."""
        dialog = EmployeeFormDialog(
            departments=self._load_department_choices(),
            branches=self._load_branch_choices(),
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["employee_number"] or not values["full_name"]:
            self.show_error("الرقم الوظيفي والاسم الكامل حقلان إلزاميان.")
            return
        result = self._controller.create_employee(**values)
        if result is not None:
            self.refresh()

    def _on_edit_row(self, row: dict[str, Any]) -> None:
        """Open the "edit employee" dialog for ``row`` and persist changes."""
        dialog = EmployeeFormDialog(
            departments=self._load_department_choices(),
            branches=self._load_branch_choices(),
            existing=row,
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["employee_number"] or not values["full_name"]:
            self.show_error("الرقم الوظيفي والاسم الكامل حقلان إلزاميان.")
            return
        result = self._controller.update_employee(row["id"], **values)
        if result is not None:
            self.refresh()

    def _on_delete_clicked(self) -> None:
        """Confirm and delete the currently selected employee."""
        row = self.selected_row()
        if row is None:
            return
        confirmed = ConfirmDialog.confirm(
            self,
            "تأكيد حذف الموظف",
            f"هل أنت متأكد من حذف الموظف \"{row['full_name']}\"؟",
            danger=True,
        )
        if not confirmed:
            return
        if self._controller.delete_employee(row["id"]):
            self.refresh()

    # ------------------------------------------------------------------
    # Device / biometric operations
    # ------------------------------------------------------------------

    def _select_device(
        self,
        *,
        protocols: tuple[DeviceProtocol, ...] | None = None,
        empty_message: str = "لا يوجد جهاز نشط متاح.",
    ) -> dict[str, Any] | None:
        """Prompt for an active device, optionally restricted to specific protocols.

        Args:
            protocols: Restrict the choices to these protocols; ``None``
                allows any active device.
            empty_message: Shown inline if no eligible device exists.

        Returns:
            The chosen device's dict, or ``None`` if the user
            cancelled or no eligible device exists (an inline error is
            already shown in that case).
        """
        devices = self._device_controller.list_devices(active_only=True)
        if protocols is not None:
            allowed = {protocol.value for protocol in protocols}
            devices = [device for device in devices if device["protocol"] in allowed]
        if not devices:
            self.show_error(empty_message)
            return None
        dialog = SelectDeviceDialog(devices=devices, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return None
        device_id = dialog.selected_device_id()
        if device_id is None:
            return None
        return next((device for device in devices if device["id"] == device_id), None)

    def _on_push_to_device_clicked(self) -> None:
        """Send the selected employee's record to a chosen device (no biometric action)."""
        row = self.selected_row()
        if row is None:
            return
        device = self._select_device()
        if device is None:
            return
        succeeded = run_blocking_operation(
            self,
            "جارٍ إرسال بيانات الموظف إلى الجهاز...",
            lambda: self._device_controller.push_employee_to_device(
                device_id=device["id"], employee_id=row["id"]
            ),
        )
        if succeeded:
            QMessageBox.information(
                self,
                "إرسال إلى الجهاز",
                f"تم إرسال بيانات الموظف \"{row['full_name']}\" إلى جهاز \"{device['name']}\" بنجاح.",
            )

    def _on_register_face_clicked(self) -> None:
        """Run the full on-device face-enrollment workflow for the selected employee.

        Works with either a face-capable ZKTeco device (heuristic
        device-wide-count workflow -- see
        :mod:`ui.face_enrollment_dialog`'s own module docstring for why
        that is the "nearest supported flow" for that protocol) or a
        DELI ES172 device (a real, documented capture-then-attach job,
        see :mod:`ui.deli_enrollment_dialog`'s own module docstring) --
        which one runs depends entirely on the device the operator
        picks.
        """
        row = self.selected_row()
        if row is None:
            return
        device = self._select_device(
            protocols=(DeviceProtocol.ZKTECO_TCP, DeviceProtocol.ZKTECO_UDP, DeviceProtocol.DELI_ES172),
            empty_message="لا يوجد جهاز يدعم تسجيل بصمة الوجه.",
        )
        if device is None:
            return

        if device["protocol"] == DeviceProtocol.DELI_ES172.value:
            self._run_deli_enrollment(row, device, kind=ENROLLMENT_KIND_FACE)
            return

        begin_result = run_blocking_operation(
            self,
            "جارٍ التحضير لتسجيل بصمة الوجه على الجهاز...",
            lambda: self._device_controller.begin_face_enrollment(
                device_id=device["id"], employee_id=row["id"]
            ),
        )
        if begin_result is None:
            return
        if begin_result["outcome"] != "started":
            self.show_error(begin_result["message_ar"])
            return

        dialog = FaceEnrollmentDialog(
            controller=self._device_controller,
            device_id=device["id"],
            employee_id=row["id"],
            enrollment_session=begin_result["session"],
            parent=self,
        )
        dialog.exec()
        final_result = dialog.final_result()
        if final_result is not None and final_result["outcome"] == "confirmed":
            self.refresh()

    def _on_register_fingerprint_deli_clicked(self) -> None:
        """Run the on-device DELI ES172 fingerprint-enrollment workflow for the selected employee."""
        row = self.selected_row()
        if row is None:
            return
        device = self._select_device(
            protocols=(DeviceProtocol.DELI_ES172,),
            empty_message="لا يوجد جهاز DELI ES172 نشط متاح.",
        )
        if device is None:
            return
        self._run_deli_enrollment(row, device, kind=ENROLLMENT_KIND_FINGERPRINT)

    def _on_register_palm_deli_clicked(self) -> None:
        """Run the on-device DELI ES172 palm-enrollment workflow for the selected employee."""
        row = self.selected_row()
        if row is None:
            return
        device = self._select_device(
            protocols=(DeviceProtocol.DELI_ES172,),
            empty_message="لا يوجد جهاز DELI ES172 نشط متاح.",
        )
        if device is None:
            return
        self._run_deli_enrollment(row, device, kind=ENROLLMENT_KIND_PALM)

    def _run_deli_enrollment(
        self, row: dict[str, Any], device: dict[str, Any], *, kind: str
    ) -> None:
        """Run the full on-device DELI ES172 biometric-enrollment workflow.

        Args:
            row: The employee row to enroll.
            device: The DELI ES172 device to enroll on.
            kind: One of ``devices.deli_es172_device.ENROLLMENT_KIND_FACE``/
                ``ENROLLMENT_KIND_FINGERPRINT``/``ENROLLMENT_KIND_PALM``.
        """
        begin_result = run_blocking_operation(
            self,
            "جارٍ بدء عملية التسجيل على الجهاز...",
            lambda: self._device_controller.begin_deli_enrollment(
                device_id=device["id"], employee_id=row["id"], kind=kind
            ),
        )
        if begin_result is None:
            return
        if begin_result["outcome"] != "started":
            self.show_error(begin_result["message_ar"])
            return

        dialog = DeliEnrollmentDialog(
            controller=self._device_controller,
            device_id=device["id"],
            employee_id=row["id"],
            employee_name=row["full_name"],
            begin_result=begin_result,
            parent=self,
        )
        dialog.exec()
        final_result = dialog.final_result()
        if final_result is not None and final_result["outcome"] == "attached":
            QMessageBox.information(self, "تسجيل البصمة الحيوية", final_result["message_ar"])
            self.refresh()

    def _on_biometric_status_clicked(self) -> None:
        """Show the selected employee's cached biometric status, with refresh/reset actions."""
        row = self.selected_row()
        if row is None:
            return
        status = self._device_controller.get_employee_biometric_status(row["id"])
        if status is None:
            return
        devices = self._device_controller.list_devices(active_only=True)
        dialog = BiometricStatusDialog(
            controller=self._device_controller,
            employee_id=row["id"],
            status=status,
            devices=devices,
            parent=self,
        )
        dialog.exec()

    # ------------------------------------------------------------------
    # Manual deductions / bonuses
    # ------------------------------------------------------------------

    def _open_adjustment_dialog(self, adjustment_type: PayrollAdjustmentType) -> None:
        """Open the shared manual deduction/bonus form for the selected employee.

        Reuses :class:`~ui.payroll.PayrollAdjustmentFormDialog` -- the
        same dialog the monthly payroll screen uses -- so there is one
        real "إضافة خصم"/"إضافة مكافأة" implementation, not a
        duplicate per entry point.
        """
        row = self.selected_row()
        if row is None:
            return
        dialog = PayrollAdjustmentFormDialog(
            employee_name=row["full_name"], default_type=adjustment_type, parent=self
        )
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["reason_text"]:
            self.show_error("وصف السبب حقل إلزامي.")
            return
        result = self._payroll_controller.add_manual_adjustment(row["id"], **values)
        if result is not None:
            label = "الخصم" if adjustment_type is PayrollAdjustmentType.DEDUCTION else "المكافأة"
            QMessageBox.information(
                self,
                "تمت الإضافة",
                f"تمت إضافة {label} للموظف \"{row['full_name']}\" بنجاح.",
            )

    def _on_add_deduction_clicked(self) -> None:
        """Add a manual deduction for the selected employee."""
        self._open_adjustment_dialog(PayrollAdjustmentType.DEDUCTION)

    def _on_add_bonus_clicked(self) -> None:
        """Add a manual bonus/addition for the selected employee."""
        self._open_adjustment_dialog(PayrollAdjustmentType.BONUS)

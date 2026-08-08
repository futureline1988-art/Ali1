"""Payroll screen: monthly payroll runs, automatic-rule settings, and manual adjustments."""

from __future__ import annotations

from datetime import date as date_cls
from decimal import Decimal, InvalidOperation
from typing import Any

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from controllers.payroll_controller import PayrollController
from models.enums import (
    PayrollAdjustmentType,
    PayrollAutoRuleType,
    PayrollBonusReason,
    PayrollCalculationMethod,
    PayrollDeductionReason,
)
from ui.table_page import TablePage
from ui.widgets import (
    ConfirmDialog,
    make_danger_button,
    make_heading_label,
    make_primary_button,
    make_secondary_label,
    make_status_label,
)

_MONTH_LABELS_AR = [
    "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
]

_CALCULATION_METHOD_ORDER = list(PayrollCalculationMethod)


def _format_currency(value: Any) -> str:
    """Render a controller-serialized amount (a numeric string) as ``"20,000.00 د.ع"``."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError):
        return str(value)
    return f"{amount:,.2f} د.ع"


class PayrollAdjustmentFormDialog(QDialog):
    """Add a manual deduction or bonus for one employee.

    Reused by both :class:`PayrollLineDetailDialog` (from the payroll
    screen) and the Employees screen's "إضافة خصم"/"إضافة مكافأة"
    toolbar actions -- one real dialog behind every entry point,
    not a duplicate per screen.
    """

    def __init__(
        self,
        *,
        employee_name: str,
        default_type: PayrollAdjustmentType = PayrollAdjustmentType.DEDUCTION,
        parent: QWidget | None = None,
    ) -> None:
        """Build the dialog.

        Args:
            employee_name: Shown in the window title for context.
            default_type: Which adjustment type is pre-selected.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle(f"إضافة خصم أو مكافأة - {employee_name}")
        self.setMinimumWidth(420)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.type_combo = QComboBox(self)
        self.type_combo.addItem(PayrollAdjustmentType.DEDUCTION.label_ar, userData=PayrollAdjustmentType.DEDUCTION)
        self.type_combo.addItem(PayrollAdjustmentType.BONUS.label_ar, userData=PayrollAdjustmentType.BONUS)
        self.type_combo.setCurrentIndex(0 if default_type is PayrollAdjustmentType.DEDUCTION else 1)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow("النوع *", self.type_combo)

        self.reason_combo = QComboBox(self)
        form.addRow("سبب (اختياري)", self.reason_combo)
        self._on_type_changed()

        self.amount_spin = QDoubleSpinBox(self)
        self.amount_spin.setRange(0.01, 999_999_999.99)
        self.amount_spin.setDecimals(2)
        self.amount_spin.setSuffix(" د.ع")
        form.addRow("المبلغ *", self.amount_spin)

        self.date_edit = QDateEdit(self)
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(QDate.currentDate())
        form.addRow("التاريخ *", self.date_edit)

        self.reason_text_edit = QLineEdit(self)
        self.reason_text_edit.setPlaceholderText("وصف السبب")
        form.addRow("الوصف *", self.reason_text_edit)

        self.notes_edit = QPlainTextEdit(self)
        self.notes_edit.setFixedHeight(60)
        form.addRow("ملاحظات", self.notes_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self)
        buttons.button(QDialogButtonBox.Save).setText("حفظ")
        buttons.button(QDialogButtonBox.Cancel).setText("إلغاء")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _on_type_changed(self) -> None:
        """Repopulate the reason dropdown to match the selected adjustment type."""
        self.reason_combo.clear()
        self.reason_combo.addItem("—", userData=None)
        adjustment_type = self._current_adjustment_type()
        reasons = (
            PayrollDeductionReason
            if adjustment_type is PayrollAdjustmentType.DEDUCTION
            else PayrollBonusReason
        )
        for reason in reasons:
            self.reason_combo.addItem(reason.label_ar, userData=reason.value)

    def _current_adjustment_type(self) -> PayrollAdjustmentType:
        """Reconstruct a real enum instance from the combo's current selection.

        Qt's QVariant round-trip degrades a ``BilingualEnum`` member (a
        str subclass) back into a plain ``str`` -- see
        ``ui/devices.py``'s own ``DeviceFormDialog.values()`` for the
        same issue -- so ``currentData()`` must always be reconstructed
        into a real enum instance here rather than trusted directly.
        """
        return PayrollAdjustmentType(self.type_combo.currentData())

    def values(self) -> dict[str, Any]:
        """Read the form's current state for
        :meth:`~controllers.payroll_controller.PayrollController.add_manual_adjustment`.
        """
        qdate = self.date_edit.date()
        adjustment_type = self._current_adjustment_type()
        raw_reason = self.reason_combo.currentData()
        reason: PayrollDeductionReason | PayrollBonusReason | None = None
        if raw_reason is not None:
            reason = (
                PayrollDeductionReason(raw_reason)
                if adjustment_type is PayrollAdjustmentType.DEDUCTION
                else PayrollBonusReason(raw_reason)
            )
        reason_text = self.reason_text_edit.text().strip()
        if not reason_text and reason is not None:
            reason_text = reason.label_ar
        return {
            "adjustment_type": adjustment_type,
            "amount": Decimal(str(self.amount_spin.value())),
            "reason_category": reason.value if reason is not None else None,
            "reason_text": reason_text,
            "adjustment_date": date_cls(qdate.year(), qdate.month(), qdate.day()),
            "notes": self.notes_edit.toPlainText().strip() or None,
        }


class PayrollRulesDialog(QDialog):
    """Configure this company's automatic payroll deduction/addition rules.

    Every attendance fact (late, absence, ...) is always recorded
    regardless of these settings -- a rule only decides whether that
    fact ever becomes an actual salary adjustment.
    """

    def __init__(self, *, rules: list[dict[str, Any]], parent: QWidget | None = None) -> None:
        """Build the dialog.

        Args:
            rules: One dict per rule type, as returned by
                :meth:`~controllers.payroll_controller.PayrollController.list_rules`.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("إعدادات الخصومات والإضافات التلقائية")
        self.setMinimumWidth(600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(12)

        intro = make_secondary_label(
            "هذه القواعد تتحكم فقط بتحويل حقائق الحضور (تأخير، غياب، انصراف مبكر، تسجيل "
            "مفقود، وقت إضافي) إلى خصم أو إضافة مالية فعلية. حقائق الحضور نفسها تُسجَّل "
            "دائماً بغض النظر عن تفعيل هذه القواعد."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._rows: dict[PayrollAutoRuleType, dict[str, QWidget]] = {}
        form = QFormLayout()
        form.setSpacing(10)
        for rule in rules:
            rule_type = PayrollAutoRuleType(rule["rule_type"])

            row_widget = QWidget(self)
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)

            enabled_checkbox = QCheckBox("مفعّل", row_widget)
            enabled_checkbox.setChecked(bool(rule["enabled"]))
            row_layout.addWidget(enabled_checkbox)

            method_combo = QComboBox(row_widget)
            for method in _CALCULATION_METHOD_ORDER:
                method_combo.addItem(method.label_ar, userData=method.value)
            current_method = PayrollCalculationMethod(rule["calculation_method"])
            method_combo.setCurrentIndex(_CALCULATION_METHOD_ORDER.index(current_method))
            row_layout.addWidget(method_combo)

            value_spin = QDoubleSpinBox(row_widget)
            value_spin.setRange(0, 999_999_999.99)
            value_spin.setDecimals(2)
            value_spin.setValue(float(rule["value"]))
            row_layout.addWidget(value_spin)

            form.addRow(rule["rule_type_label_ar"], row_widget)
            self._rows[rule_type] = {
                "enabled": enabled_checkbox,
                "method": method_combo,
                "value": value_spin,
            }
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self)
        buttons.button(QDialogButtonBox.Save).setText("حفظ")
        buttons.button(QDialogButtonBox.Cancel).setText("إلغاء")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[PayrollAutoRuleType, dict[str, Any]]:
        """Read every rule row's current configuration.

        Returns:
            A mapping from rule type to keyword arguments for
            :meth:`~controllers.payroll_controller.PayrollController.set_rule`.
        """
        return {
            rule_type: {
                "enabled": widgets["enabled"].isChecked(),
                "calculation_method": PayrollCalculationMethod(widgets["method"].currentData()),
                "value": Decimal(str(widgets["value"].value())),
            }
            for rule_type, widgets in self._rows.items()
        }


class PayrollLineDetailDialog(QDialog):
    """One employee's full payroll breakdown for one period, with adjustment management.

    Adding or cancelling an adjustment here immediately triggers a
    recompute through the controller (not just a local UI update), so
    the displayed figures are never stale relative to the database.
    """

    changed = Signal()
    """Emitted if any adjustment was added or cancelled, so the payroll
    screen behind this dialog knows to refresh when it closes."""

    def __init__(
        self,
        *,
        controller: PayrollController,
        line: dict[str, Any],
        run_id: int,
        year: int,
        month: int,
        run_status: str,
        adjustments: list[dict[str, Any]],
        parent: QWidget | None = None,
    ) -> None:
        """Build the dialog.

        Args:
            controller: The payroll controller to act through.
            line: The employee's computed payroll line.
            run_id: The owning payroll run's id.
            year: The pay period's calendar year.
            month: The pay period's calendar month.
            run_status: The run's current status
                (``"draft"``/``"reviewed"``/``"finalized"``) -- adding/
                cancelling adjustments is disabled once finalized.
            adjustments: This employee's adjustments for the period.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._controller = controller
        self._employee_id = line["employee_id"]
        self._employee_name = line["employee_name"]
        self._run_id = run_id
        self._year = year
        self._month = month
        self._locked = run_status == "finalized"
        self._changed = False

        self.setWindowTitle(f"تفاصيل الراتب - {line['employee_name']}")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(12)

        if self._locked:
            lock_notice = make_secondary_label(
                "هذا الشهر معتمد نهائياً. لإضافة أو إلغاء خصومات/مكافآت، أعد فتح الرواتب أولاً."
            )
            lock_notice.setWordWrap(True)
            layout.addWidget(lock_notice)

        self._summary_form = QFormLayout()
        self._summary_labels: dict[str, QLabel] = {}
        for key, label_ar in (
            ("base_salary", "الراتب الأساسي"),
            ("working_days_in_period", "أيام العمل في الشهر"),
            ("attendance_days", "أيام الحضور"),
            ("absence_days", "أيام الغياب"),
            ("missing_punch_days", "أيام بتسجيل مفقود"),
            ("late_minutes_total", "إجمالي دقائق التأخير"),
            ("early_leave_minutes_total", "إجمالي دقائق الانصراف المبكر"),
            ("overtime_minutes_total", "إجمالي دقائق الوقت الإضافي"),
            ("automatic_deduction_total", "الخصومات التلقائية"),
            ("manual_deduction_total", "الخصومات اليدوية"),
            ("bonus_total", "الإضافات/المكافآت"),
            ("net_salary", "صافي الراتب"),
        ):
            value_label = QLabel(self)
            if key == "net_salary":
                value_label.setStyleSheet("font-weight: bold;")
            self._summary_labels[key] = value_label
            self._summary_form.addRow(label_ar, value_label)
        layout.addLayout(self._summary_form)
        self._apply_line(line)

        layout.addWidget(make_heading_label("الخصومات والمكافآت"))
        self.table = QTableWidget(self)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["النوع", "المصدر", "المبلغ", "السبب", ""])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table, stretch=1)

        self._adjustments: list[dict[str, Any]] = []
        self._set_adjustments(adjustments)

        buttons_row = QHBoxLayout()
        self.add_deduction_button = make_primary_button("إضافة خصم", parent=self)
        self.add_deduction_button.setEnabled(not self._locked)
        self.add_deduction_button.clicked.connect(
            lambda: self._open_adjustment_form(PayrollAdjustmentType.DEDUCTION)
        )
        buttons_row.addWidget(self.add_deduction_button)

        self.add_bonus_button = make_primary_button("إضافة مكافأة", parent=self)
        self.add_bonus_button.setEnabled(not self._locked)
        self.add_bonus_button.clicked.connect(
            lambda: self._open_adjustment_form(PayrollAdjustmentType.BONUS)
        )
        buttons_row.addWidget(self.add_bonus_button)
        buttons_row.addStretch(1)

        close_button = QPushButton("إغلاق", self)
        close_button.clicked.connect(self.reject)
        buttons_row.addWidget(close_button)
        layout.addLayout(buttons_row)

    def _apply_line(self, line: dict[str, Any]) -> None:
        """Update the summary labels from a fresh payroll line dict."""
        currency_keys = {
            "base_salary", "automatic_deduction_total", "manual_deduction_total",
            "bonus_total", "net_salary",
        }
        for key, label_widget in self._summary_labels.items():
            value = line.get(key)
            label_widget.setText(_format_currency(value) if key in currency_keys else str(value))

    def _set_adjustments(self, adjustments: list[dict[str, Any]]) -> None:
        """Replace the adjustments table's contents."""
        self._adjustments = adjustments
        self.table.setRowCount(len(adjustments))
        for row_index, adjustment in enumerate(adjustments):
            self.table.setItem(row_index, 0, QTableWidgetItem(adjustment["adjustment_type_label_ar"]))
            self.table.setItem(row_index, 1, QTableWidgetItem(adjustment["source_label_ar"]))
            self.table.setItem(row_index, 2, QTableWidgetItem(_format_currency(adjustment["amount"])))
            self.table.setItem(row_index, 3, QTableWidgetItem(adjustment["reason_text"]))
            if self._locked:
                self.table.setCellWidget(row_index, 4, QWidget(self.table))
                continue
            cancel_button = make_danger_button("إلغاء", parent=self.table)
            adjustment_id = adjustment["id"]
            cancel_button.clicked.connect(
                lambda _checked, aid=adjustment_id: self._on_cancel_adjustment(aid)
            )
            self.table.setCellWidget(row_index, 4, cancel_button)

    def _open_adjustment_form(self, adjustment_type: PayrollAdjustmentType) -> None:
        """Open the add-adjustment dialog and, if confirmed, persist and refresh."""
        if self._locked:
            return
        dialog = PayrollAdjustmentFormDialog(
            employee_name=self._employee_name, default_type=adjustment_type, parent=self
        )
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        if not values["reason_text"]:
            return
        result = self._controller.add_manual_adjustment(self._employee_id, **values)
        if result is not None:
            self._changed = True
            self._refresh()

    def _on_cancel_adjustment(self, adjustment_id: int) -> None:
        """Confirm and cancel one adjustment, then refresh."""
        if self._locked:
            return
        confirmed = ConfirmDialog.confirm(
            self, "تأكيد الإلغاء", "هل تريد إلغاء هذا الخصم/المكافأة؟", danger=True
        )
        if not confirmed:
            return
        if self._controller.cancel_adjustment(adjustment_id):
            self._changed = True
            self._refresh()

    def _refresh(self) -> None:
        """Recompute the payroll run and reload this employee's line/adjustments."""
        self._controller.compute_payroll_run(year=self._year, month=self._month)
        lines = self._controller.list_lines(self._run_id)
        updated = next((row for row in lines if row["employee_id"] == self._employee_id), None)
        if updated is not None:
            self._apply_line(updated)
        adjustments = self._controller.list_adjustments_for_employee_period(
            self._employee_id, year=self._year, month=self._month
        )
        self._set_adjustments(adjustments)

    def reject(self) -> None:
        """Emit :attr:`changed` before closing if anything was modified.

        Covers both the "إغلاق" button and the window's own close
        button -- ``QDialog``'s default response to the latter is to
        call :meth:`reject` too, so no separate ``closeEvent`` override
        is needed.
        """
        if self._changed:
            self.changed.emit()
            self._changed = False
        super().reject()


class PayrollPage(TablePage):
    """The monthly payroll computation and review screen."""

    def __init__(
        self,
        *,
        company_id: int,
        current_user_id: int | None = None,
        permission_codes: frozenset[str] = frozenset(),
        parent: QWidget | None = None,
    ) -> None:
        """Create the payroll page.

        Args:
            company_id: The company this screen manages payroll for.
            current_user_id: The signed-in user, for audit attribution.
            permission_codes: The signed-in user's granted permission
                codes.
            parent: Optional parent widget.
        """
        super().__init__(
            title="الرواتب", add_button_text=None, search_placeholder="بحث باسم الموظف...", parent=parent
        )
        self._controller = PayrollController(
            company_id=company_id,
            actor_user_id=current_user_id,
            permission_codes=permission_codes,
        )
        self._controller.operation_failed.connect(self.show_error)

        today = date_cls.today()
        self.month_combo = QComboBox(self)
        for index, label in enumerate(_MONTH_LABELS_AR, start=1):
            self.month_combo.addItem(label, userData=index)
        self.month_combo.setCurrentIndex(today.month - 1)
        self.month_combo.currentIndexChanged.connect(self.refresh)
        self.toolbar_layout.addWidget(self.month_combo)

        self.year_spin = QSpinBox(self)
        self.year_spin.setRange(2000, 2100)
        self.year_spin.setValue(today.year)
        self.year_spin.valueChanged.connect(self.refresh)
        self.toolbar_layout.addWidget(self.year_spin)

        self.compute_button = make_primary_button("حساب/تحديث الرواتب", parent=self)
        self.compute_button.clicked.connect(self._on_compute_clicked)
        self.toolbar_layout.addWidget(self.compute_button)

        self.details_button = QPushButton("عرض التفاصيل", self)
        self.details_button.setEnabled(False)
        self.details_button.clicked.connect(self._on_view_details)
        self.toolbar_layout.addWidget(self.details_button)

        self.rules_button = QPushButton("إعدادات الخصومات التلقائية", self)
        self.rules_button.clicked.connect(self._on_rules_clicked)
        self.toolbar_layout.addWidget(self.rules_button)

        self.review_button = QPushButton("تحديد كمُراجَع", self)
        self.review_button.clicked.connect(self._on_review_clicked)
        self.toolbar_layout.addWidget(self.review_button)

        self.finalize_button = make_primary_button("اعتماد نهائي", parent=self)
        self.finalize_button.clicked.connect(self._on_finalize_clicked)
        self.toolbar_layout.addWidget(self.finalize_button)

        self.reopen_button = make_danger_button("إعادة فتح", parent=self)
        self.reopen_button.clicked.connect(self._on_reopen_clicked)
        self.toolbar_layout.addWidget(self.reopen_button)

        self.status_label = make_status_label("", "info")
        self.toolbar_layout.addWidget(self.status_label)

        self.set_columns(
            [
                ("employee_name", "الموظف", lambda r: r.get("employee_name") or ""),
                ("employee_number", "الرقم الوظيفي", lambda r: r.get("employee_number") or ""),
                ("base_salary", "الراتب الأساسي", lambda r: _format_currency(r.get("base_salary"))),
                ("attendance_days", "أيام الحضور", lambda r: str(r.get("attendance_days", 0))),
                ("absence_days", "أيام الغياب", lambda r: str(r.get("absence_days", 0))),
                ("late_minutes_total", "دقائق التأخير", lambda r: str(r.get("late_minutes_total", 0))),
                (
                    "overtime_minutes_total",
                    "الوقت الإضافي (دقيقة)",
                    lambda r: str(r.get("overtime_minutes_total", 0)),
                ),
                (
                    "automatic_deduction_total",
                    "خصومات تلقائية",
                    lambda r: _format_currency(r.get("automatic_deduction_total")),
                ),
                (
                    "manual_deduction_total",
                    "خصومات يدوية",
                    lambda r: _format_currency(r.get("manual_deduction_total")),
                ),
                ("bonus_total", "مكافآت", lambda r: _format_currency(r.get("bonus_total"))),
                ("net_salary", "صافي الراتب", lambda r: _format_currency(r.get("net_salary"))),
            ]
        )

        self.row_activated.connect(lambda _row: self._on_view_details())
        self.search_changed.connect(self._on_search_changed)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        self._current_run: dict[str, Any] | None = None
        self._all_lines: list[dict[str, Any]] = []
        self.refresh()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _selected_period(self) -> tuple[int, int]:
        """Return the currently selected ``(year, month)`` from the toolbar pickers."""
        return self.year_spin.value(), self.month_combo.currentData()

    def refresh(self) -> None:
        """Reload the selected period's payroll run and its lines."""
        self.clear_error()
        year, month = self._selected_period()
        self._current_run = self._controller.get_run_for_period(year=year, month=month)
        if self._current_run is None:
            self._all_lines = []
            self.status_label.setText("لم يتم حساب رواتب هذا الشهر بعد.")
        else:
            self._all_lines = self._controller.list_lines(self._current_run["id"])
            self.status_label.setText(f"الحالة: {self._current_run['status_label_ar']}")
        self.populate(self._all_lines)
        self._on_selection_changed()
        self._update_action_buttons()

    def _update_action_buttons(self) -> None:
        """Enable/disable the lifecycle buttons based on the current run's status."""
        status = self._current_run["status"] if self._current_run else None
        self.review_button.setEnabled(status == "draft")
        self.finalize_button.setEnabled(status in ("draft", "reviewed"))
        self.reopen_button.setEnabled(status == "finalized")

    def _on_search_changed(self, query: str) -> None:
        """Filter the currently loaded lines by employee name (client-side)."""
        self.clear_error()
        rows = self._all_lines
        if query.strip():
            needle = query.strip().lower()
            rows = [row for row in rows if needle in (row.get("employee_name") or "").lower()]
        self.populate(rows)
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        """Enable/disable the details button based on the current selection."""
        self.details_button.setEnabled(self.selected_row() is not None)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_compute_clicked(self) -> None:
        """Compute (or recompute) the selected period's payroll run."""
        year, month = self._selected_period()
        result = self._controller.compute_payroll_run(year=year, month=month)
        if result is not None:
            self.refresh()

    def _on_rules_clicked(self) -> None:
        """Open the automatic-rules settings dialog and persist any changes."""
        rules = self._controller.list_rules()
        if not rules:
            return
        dialog = PayrollRulesDialog(rules=rules, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        all_saved = True
        for rule_type, values in dialog.values().items():
            if self._controller.set_rule(rule_type, **values) is None:
                all_saved = False
        if not all_saved:
            # A failed save already left its message on screen via the
            # operation_failed -> show_error connection above -- clearing
            # it here would silently hide the only feedback the manager
            # gets that a rule was NOT saved.
            return
        self.clear_error()
        QMessageBox.information(self, "قواعد الرواتب", "تم حفظ إعدادات القواعد التلقائية بنجاح.")

    def _on_review_clicked(self) -> None:
        """Mark the current run as reviewed."""
        if self._current_run is None:
            return
        if self._controller.mark_reviewed(self._current_run["id"]) is not None:
            self.refresh()

    def _on_finalize_clicked(self) -> None:
        """Confirm and finalize the current run, freezing an immutable snapshot."""
        if self._current_run is None:
            return
        confirmed = ConfirmDialog.confirm(
            self,
            "تأكيد الاعتماد النهائي",
            "بعد الاعتماد النهائي لن يمكن تعديل خصومات أو مكافآت هذا الشهر إلا بإعادة "
            "فتحه صراحةً. هل تريد المتابعة؟",
            danger=True,
        )
        if not confirmed:
            return
        if self._controller.finalize_payroll_run(self._current_run["id"]) is not None:
            self.refresh()

    def _on_reopen_clicked(self) -> None:
        """Confirm and reopen the current, finalized run for correction."""
        if self._current_run is None:
            return
        confirmed = ConfirmDialog.confirm(
            self,
            "تأكيد إعادة الفتح",
            "سيتم إعادة فتح رواتب هذا الشهر للتعديل. النسخة المعتمدة السابقة تبقى محفوظة "
            "في السجل التاريخي. هل تريد المتابعة؟",
            danger=True,
        )
        if not confirmed:
            return
        if self._controller.reopen_payroll_run(self._current_run["id"]) is not None:
            self.refresh()

    def _on_view_details(self) -> None:
        """Open the selected employee's full payroll breakdown for this period."""
        row = self.selected_row()
        if row is None or self._current_run is None:
            return
        adjustments = self._controller.list_adjustments_for_employee_period(
            row["employee_id"], year=self._current_run["year"], month=self._current_run["month"]
        )
        dialog = PayrollLineDetailDialog(
            controller=self._controller,
            line=row,
            run_id=self._current_run["id"],
            year=self._current_run["year"],
            month=self._current_run["month"],
            run_status=self._current_run["status"],
            adjustments=adjustments,
            parent=self,
        )
        dialog.changed.connect(self.refresh)
        dialog.exec()

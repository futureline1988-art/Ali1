"""Attendance screen: view records, record manual punches, and compute daily attendance."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from PySide6.QtCore import QDate, QDateTime, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from controllers.attendance_controller import AttendanceController
from controllers.employee_controller import EmployeeController
from controllers.settings_controller import SettingsController
from models.enums import PunchType
from ui.widgets import (
    Card,
    make_heading_label,
    make_primary_button,
    make_secondary_label,
    make_status_label,
)

_DEFAULT_TIMEZONE = "Asia/Baghdad"
_ALL_EMPLOYEES = None


class AttendancePage(QWidget):
    """The attendance viewing, manual-entry, and computation screen."""

    def __init__(self, *, company_id: int, parent: QWidget | None = None) -> None:
        """Create the attendance page.

        Args:
            company_id: The company this screen manages attendance for.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._company_id = company_id
        self._controller = AttendanceController(company_id=company_id)
        self._controller.operation_failed.connect(self._show_error)
        self._employee_controller = EmployeeController(company_id=company_id)
        self._timezone = self._load_company_timezone()
        self._employees: list[dict[str, Any]] = []
        self._records: list[dict[str, Any]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        layout.addWidget(make_heading_label("الحضور والانصراف"))

        self._status_label = make_status_label("", "danger")
        self._status_label.hide()
        layout.addWidget(self._status_label)

        layout.addWidget(self._build_filter_card())
        layout.addWidget(self._build_manual_punch_card())

        self.table = QTableWidget(self)
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["الموظف", "التاريخ", "الدخول", "الخروج", "الحالة", "دقائق التأخير", "ساعات العمل"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table, stretch=1)

        self._reload_employees()
        self._load_records()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _load_company_timezone(self) -> ZoneInfo:
        """Resolve this company's configured timezone, for display conversion only."""
        settings_controller = SettingsController(company_id=self._company_id)
        settings = settings_controller.get_settings()
        tz_name = (settings or {}).get("timezone_name") or _DEFAULT_TIMEZONE
        try:
            return ZoneInfo(tz_name)
        except Exception:  # noqa: BLE001 - fall back rather than fail to open the page
            return ZoneInfo(_DEFAULT_TIMEZONE)

    def _build_filter_card(self) -> Card:
        """Build the "view records" filter row: employee + date range + load."""
        card = Card(parent=self)
        row = QHBoxLayout()
        row.setSpacing(10)

        row.addWidget(make_secondary_label("الموظف"))
        self.filter_employee_combo = QComboBox(card)
        self.filter_employee_combo.addItem("جميع الموظفين", userData=_ALL_EMPLOYEES)
        self.filter_employee_combo.setMinimumWidth(200)
        row.addWidget(self.filter_employee_combo)

        row.addWidget(make_secondary_label("من"))
        self.start_date_edit = QDateEdit(card)
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate.currentDate().addDays(-6))
        row.addWidget(self.start_date_edit)

        row.addWidget(make_secondary_label("إلى"))
        self.end_date_edit = QDateEdit(card)
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDate(QDate.currentDate())
        row.addWidget(self.end_date_edit)

        load_button = make_primary_button("عرض", parent=card)
        load_button.clicked.connect(self._load_records)
        row.addWidget(load_button)

        row.addStretch(1)

        compute_selected_button = QPushButton("احتساب اليوم للموظف المحدد", card)
        compute_selected_button.clicked.connect(self._on_compute_selected_employee)
        row.addWidget(compute_selected_button)

        compute_all_button = QPushButton("احتساب اليوم لجميع الموظفين", card)
        compute_all_button.clicked.connect(self._on_compute_all_employees)
        row.addWidget(compute_all_button)

        card.body_layout.addLayout(row)
        return card

    def _build_manual_punch_card(self) -> Card:
        """Build the manual-punch entry form."""
        card = Card(parent=self)
        card.body_layout.addWidget(make_secondary_label("تسجيل بصمة يدوية"))

        row = QHBoxLayout()
        row.setSpacing(10)

        self.punch_employee_combo = QComboBox(card)
        row.addWidget(self.punch_employee_combo)

        self.punch_type_combo = QComboBox(card)
        self.punch_type_combo.addItem("دخول", userData=PunchType.CHECK_IN)
        self.punch_type_combo.addItem("خروج", userData=PunchType.CHECK_OUT)
        row.addWidget(self.punch_type_combo)

        self.use_now_checkbox = QCheckBox("استخدام الوقت الحالي", card)
        self.use_now_checkbox.setChecked(True)
        self.use_now_checkbox.toggled.connect(self._on_use_now_toggled)
        row.addWidget(self.use_now_checkbox)

        self.punch_datetime_edit = QDateTimeEdit(card)
        self.punch_datetime_edit.setCalendarPopup(True)
        self.punch_datetime_edit.setDateTime(QDateTime.currentDateTime())
        self.punch_datetime_edit.setEnabled(False)
        row.addWidget(self.punch_datetime_edit)

        self.punch_notes_edit = QLineEdit(card)
        self.punch_notes_edit.setPlaceholderText("ملاحظات (اختياري)")
        row.addWidget(self.punch_notes_edit, stretch=1)

        record_button = make_primary_button("تسجيل", parent=card)
        record_button.clicked.connect(self._on_record_manual_punch)
        row.addWidget(record_button)

        card.body_layout.addLayout(row)
        return card

    # ------------------------------------------------------------------
    # Employees
    # ------------------------------------------------------------------

    def _reload_employees(self) -> None:
        """Populate both employee pickers from the employee list."""
        self._employees = self._employee_controller.list_employees(active_only=True)

        self.filter_employee_combo.clear()
        self.filter_employee_combo.addItem("جميع الموظفين", userData=_ALL_EMPLOYEES)
        self.punch_employee_combo.clear()
        for employee in self._employees:
            label = f"{employee['full_name']} ({employee['employee_number']})"
            self.filter_employee_combo.addItem(label, userData=employee["id"])
            self.punch_employee_combo.addItem(label, userData=employee["id"])

    # ------------------------------------------------------------------
    # Loading / rendering records
    # ------------------------------------------------------------------

    def _load_records(self) -> None:
        """Fetch and display records for the selected employee/date range."""
        self._hide_error()
        start = self.start_date_edit.date().toPython()
        end = self.end_date_edit.date().toPython()
        employee_id = self.filter_employee_combo.currentData()

        if employee_id is _ALL_EMPLOYEES:
            records = self._controller.list_for_company_between(start, end)
        else:
            records = self._controller.list_for_employee_between(employee_id, start, end)

        self._records = records
        self._render_table()

    def _render_table(self) -> None:
        """Repaint :attr:`table` from :attr:`_records`."""
        self.table.setRowCount(len(self._records))
        for row_index, record in enumerate(self._records):
            values = [
                record.get("employee_full_name", ""),
                record.get("work_date", ""),
                self._format_local_time(record.get("check_in_time")),
                self._format_local_time(record.get("check_out_time")),
                record.get("status_label_ar", ""),
                str(record.get("late_minutes", 0)),
                f"{record.get('worked_hours', 0):.2f}",
            ]
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row_index, column_index, item)

    def _format_local_time(self, iso_value: str | None) -> str:
        """Convert a UTC ISO datetime string to a local ``HH:MM`` display string.

        Args:
            iso_value: An ISO-8601 datetime string (UTC), or ``None``.

        Returns:
            The time formatted in this company's configured timezone,
            or ``"—"`` if ``iso_value`` is ``None``.
        """
        if not iso_value:
            return "—"
        parsed = datetime.fromisoformat(iso_value)
        local = parsed.astimezone(self._timezone)
        return local.strftime("%H:%M")

    # ------------------------------------------------------------------
    # Manual punch
    # ------------------------------------------------------------------

    def _on_use_now_toggled(self, checked: bool) -> None:
        """Enable the custom datetime picker only when not using "now"."""
        self.punch_datetime_edit.setEnabled(not checked)

    def _on_record_manual_punch(self) -> None:
        """Record a manual punch for the selected employee."""
        self._hide_error()
        employee_id = self.punch_employee_combo.currentData()
        if employee_id is None:
            self._show_error("الرجاء اختيار الموظف.")
            return

        # Qt's QVariant round-trip degrades a BilingualEnum member (a
        # str subclass) back into a plain str, so it must be
        # reconstructed into a real PunchType here rather than trusting
        # currentData()'s type.
        punch_type = PunchType(self.punch_type_combo.currentData())
        punch_time: datetime | None = None
        if not self.use_now_checkbox.isChecked():
            qdt = self.punch_datetime_edit.dateTime()
            local_naive = qdt.toPython()
            local_aware = local_naive.replace(tzinfo=self._timezone)
            punch_time = local_aware.astimezone(timezone.utc)

        notes = self.punch_notes_edit.text().strip() or None
        success = self._controller.record_manual_punch(
            employee_id=employee_id, punch_type=punch_type, punch_time=punch_time, notes=notes
        )
        if success:
            self.punch_notes_edit.clear()
            self._load_records()

    # ------------------------------------------------------------------
    # Computation
    # ------------------------------------------------------------------

    def _on_compute_selected_employee(self) -> None:
        """Compute today's attendance for the filter's selected employee."""
        self._hide_error()
        employee_id = self.filter_employee_combo.currentData()
        if employee_id is _ALL_EMPLOYEES:
            self._show_error("الرجاء اختيار موظف محدد أولاً (وليس \"جميع الموظفين\").")
            return
        result = self._controller.compute_daily_attendance(
            employee_id=employee_id, work_date=date.today()
        )
        if result is not None:
            self._load_records()

    def _on_compute_all_employees(self) -> None:
        """Compute today's attendance for every active employee."""
        self._hide_error()
        results = self._controller.bulk_compute_for_date(date.today())
        if results:
            self._load_records()

    # ------------------------------------------------------------------
    # Errors
    # ------------------------------------------------------------------

    def _show_error(self, message: str) -> None:
        """Display an inline error banner."""
        self._status_label.setText(message)
        self._status_label.show()

    def _hide_error(self) -> None:
        """Hide the inline error banner."""
        self._status_label.clear()
        self._status_label.hide()

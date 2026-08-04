"""Reporting & Analytics page: pick a report, filter/sort/group it, chart it, export it.

Mirrors ``ui/reports.py``'s (Attendance Client) established
pick-type → pick-format → build ``(rows, columns)`` → dispatch to the
matching exporter → save via ``QFileDialog`` flow, and
:mod:`developer_suite.ui.update_manager_page`'s "background reload
failures go to :attr:`status_label`, never a blocking dialog" service/
UI boundary discipline — this page never queries a repository or the
Attendance Server directly, only
:class:`~developer_suite.services.reporting_service.ReportingService`.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from developer_suite.config import DeveloperSuiteConfig
from developer_suite.services.reporting_service import (
    REPORT_CATEGORY_LABELS_AR,
    ReportCategory,
    ReportFilters,
    ReportingService,
    ReportingServiceError,
    ReportResult,
    group_and_count,
)
from developer_suite.ui.reporting_charts import CategoryCountBarChart, CategoryCountPieChart
from models.enums import ReportFormat
from utils.csv_export import export_to_csv
from utils.excel import export_to_excel
from utils.pdf import export_to_pdf

_NO_SORT = "__no_sort__"
_NO_GROUPING = "__no_grouping__"

_FORMAT_EXTENSIONS = {
    ReportFormat.EXCEL: "xlsx",
    ReportFormat.PDF: "pdf",
    ReportFormat.CSV: "csv",
}
_FORMAT_FILE_FILTERS = {
    ReportFormat.EXCEL: "Excel Files (*.xlsx)",
    ReportFormat.PDF: "PDF Files (*.pdf)",
    ReportFormat.CSV: "CSV Files (*.csv)",
}


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return str(value)


class ReportingPage(QWidget):
    """Build, filter, chart, and export any of the 8 Phase 15 report categories."""

    def __init__(
        self,
        reporting_service: ReportingService,
        config: DeveloperSuiteConfig,
        *,
        parent: QWidget | None = None,
    ) -> None:
        """Build the Reporting & Analytics page.

        Args:
            reporting_service: Performs every report-assembly/filter/
                group operation this page displays.
            config: Supplies :attr:`~developer_suite.config.DeveloperSuiteConfig.paths`'
                ``assets_dir`` for the PDF exporter's bundled font.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._reporting_service = reporting_service
        self._config = config
        self._current_result: ReportResult | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)

        heading = QLabel("التقارير والتحليلات")
        heading_font = heading.font()
        heading_font.setPointSize(15)
        heading_font.setBold(True)
        heading.setFont(heading_font)
        outer.addWidget(heading)

        self.status_label = QLabel("", self)
        self.status_label.setStyleSheet("color: #B91C1C;")
        outer.addWidget(self.status_label)

        outer.addWidget(self._build_filter_bar())

        splitter = QSplitter(Qt.Vertical, self)
        outer.addWidget(splitter, stretch=1)

        self.results_table = QTableWidget(self)
        self.results_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.results_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        splitter.addWidget(self.results_table)

        chart_box = QGroupBox("الرسم البياني", self)
        chart_layout = QVBoxLayout(chart_box)
        self.bar_chart = CategoryCountBarChart(parent=chart_box)
        self.pie_chart = CategoryCountPieChart(parent=chart_box)
        self.pie_chart.hide()
        chart_layout.addWidget(self.bar_chart)
        chart_layout.addWidget(self.pie_chart)
        splitter.addWidget(chart_box)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        outer.addWidget(self._build_export_bar())

        self._on_category_changed()

    # -- Layout builders -------------------------------------------------------

    def _build_filter_bar(self) -> QGroupBox:
        box = QGroupBox("خيارات التقرير", self)
        form = QFormLayout()
        form.setSpacing(10)

        self.category_combo = QComboBox(box)
        for category in ReportCategory:
            self.category_combo.addItem(REPORT_CATEGORY_LABELS_AR[category], userData=category.value)
        self.category_combo.currentIndexChanged.connect(self._on_category_changed)
        form.addRow("نوع التقرير", self.category_combo)

        self.search_edit = QLineEdit(box)
        self.search_edit.setPlaceholderText("بحث في كل الأعمدة...")
        form.addRow("بحث", self.search_edit)

        date_row = QHBoxLayout()
        self.date_range_checkbox = QCheckBox("تفعيل النطاق الزمني", box)
        self.start_date_edit = QDateEdit(box)
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate.currentDate().addMonths(-1))
        self.end_date_edit = QDateEdit(box)
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDate(QDate.currentDate())
        date_row.addWidget(self.date_range_checkbox)
        date_row.addWidget(QLabel("من"))
        date_row.addWidget(self.start_date_edit)
        date_row.addWidget(QLabel("إلى"))
        date_row.addWidget(self.end_date_edit)
        form.addRow("النطاق الزمني", date_row)

        sort_row = QHBoxLayout()
        self.sort_by_combo = QComboBox(box)
        self.sort_descending_checkbox = QCheckBox("تنازلي", box)
        sort_row.addWidget(self.sort_by_combo)
        sort_row.addWidget(self.sort_descending_checkbox)
        form.addRow("الترتيب حسب", sort_row)

        self.group_by_combo = QComboBox(box)
        form.addRow("التجميع حسب", self.group_by_combo)

        self.chart_type_combo = QComboBox(box)
        self.chart_type_combo.addItem("أعمدة", userData="bar")
        self.chart_type_combo.addItem("دائري", userData="pie")
        form.addRow("نوع الرسم البياني", self.chart_type_combo)

        generate_button = QPushButton("توليد التقرير", box)
        generate_button.clicked.connect(self._on_generate_clicked)

        box_layout = QVBoxLayout(box)
        box_layout.addLayout(form)
        box_layout.addWidget(generate_button)
        return box

    def _build_export_bar(self) -> QWidget:
        bar = QWidget(self)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)

        self.result_summary_label = QLabel("", bar)
        layout.addWidget(self.result_summary_label, stretch=1)

        self.format_combo = QComboBox(bar)
        for report_format in ReportFormat:
            self.format_combo.addItem(report_format.label_ar, userData=report_format)
        layout.addWidget(self.format_combo)

        export_button = QPushButton("تصدير", bar)
        export_button.clicked.connect(self._on_export_clicked)
        layout.addWidget(export_button)
        return bar

    # -- Behavior -------------------------------------------------------------

    def _current_category(self) -> ReportCategory:
        return ReportCategory(self.category_combo.currentData())

    def _on_category_changed(self) -> None:
        category = self._current_category()
        applies_date_range = category is not ReportCategory.EXECUTIVE_DASHBOARD
        self.date_range_checkbox.setEnabled(applies_date_range)
        self.start_date_edit.setEnabled(applies_date_range and self.date_range_checkbox.isChecked())
        self.end_date_edit.setEnabled(applies_date_range and self.date_range_checkbox.isChecked())
        self._on_generate_clicked()

    def _current_filters(self) -> ReportFilters:
        use_date_range = self.date_range_checkbox.isChecked() and self.date_range_checkbox.isEnabled()
        sort_by = self.sort_by_combo.currentData()
        return ReportFilters(
            search=self.search_edit.text(),
            start_date=self.start_date_edit.date().toPython() if use_date_range else None,
            end_date=self.end_date_edit.date().toPython() if use_date_range else None,
            sort_by=sort_by if sort_by and sort_by != _NO_SORT else None,
            sort_descending=self.sort_descending_checkbox.isChecked(),
        )

    def _on_generate_clicked(self) -> None:
        category = self._current_category()
        try:
            result = self._reporting_service.build_report(category, self._current_filters())
        except ReportingServiceError as exc:
            self.status_label.setText(f"تعذّر توليد التقرير: {exc}")
            self._current_result = None
            self.results_table.setRowCount(0)
            self.results_table.setColumnCount(0)
            self.result_summary_label.setText("")
            return

        self.status_label.setText("")
        self._current_result = result
        self._populate_sort_and_group_combos(result.columns)
        self._populate_table(result)
        self._update_chart(result)
        self.result_summary_label.setText(f"عرض {len(result.rows)} من أصل {result.total_before_filters}")

    def _populate_sort_and_group_combos(self, columns: list[tuple[str, str]]) -> None:
        for combo, none_sentinel in ((self.sort_by_combo, _NO_SORT), (self.group_by_combo, _NO_GROUPING)):
            previous = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("بدون", userData=none_sentinel)
            for key, label in columns:
                combo.addItem(label, userData=key)
            restored_index = combo.findData(previous)
            combo.setCurrentIndex(restored_index if restored_index >= 0 else 0)
            combo.blockSignals(False)

    def _populate_table(self, result: ReportResult) -> None:
        table = self.results_table
        table.setColumnCount(len(result.columns))
        table.setHorizontalHeaderLabels([label for _key, label in result.columns])
        table.setRowCount(len(result.rows))
        for row_index, row in enumerate(result.rows):
            for column_index, (key, _label) in enumerate(result.columns):
                table.setItem(row_index, column_index, QTableWidgetItem(_cell_text(row.get(key))))

    def _update_chart(self, result: ReportResult) -> None:
        group_by = self.group_by_combo.currentData()
        if not group_by or group_by == _NO_GROUPING:
            self.bar_chart.hide()
            self.pie_chart.hide()
            return

        label = dict(result.columns).get(group_by, group_by)
        grouped = group_and_count(result.rows, group_by=group_by, label=label)
        chart_type = self.chart_type_combo.currentData()
        title = f"{REPORT_CATEGORY_LABELS_AR[self._current_category()]} — {label}"
        if chart_type == "pie":
            self.bar_chart.hide()
            self.pie_chart.set_title(title)
            self.pie_chart.set_data(grouped.rows)
            self.pie_chart.show()
        else:
            self.pie_chart.hide()
            self.bar_chart.set_title(title)
            self.bar_chart.set_data(grouped.rows)
            self.bar_chart.show()

    def _on_export_clicked(self) -> None:
        if self._current_result is None or not self._current_result.rows:
            QMessageBox.information(self, "تصدير", "لا توجد بيانات لتصديرها. يرجى توليد التقرير أولاً.")
            return

        report_format: ReportFormat = self.format_combo.currentData()
        category = self._current_category()
        default_name = f"{category.value}.{_FORMAT_EXTENSIONS[report_format]}"
        output_path, _selected_filter = QFileDialog.getSaveFileName(
            self, "حفظ التقرير", default_name, _FORMAT_FILE_FILTERS[report_format]
        )
        if not output_path:
            return

        try:
            self._export(Path(output_path), report_format, self._current_result, title=REPORT_CATEGORY_LABELS_AR[category])
        except Exception as exc:  # noqa: BLE001 - surface any exporter failure to the user, never crash the page
            QMessageBox.warning(self, "تعذّر التصدير", str(exc))
            return
        QMessageBox.information(self, "تم التصدير", f"تم حفظ التقرير في:\n{output_path}")

    def _export(self, output_path: Path, report_format: ReportFormat, result: ReportResult, *, title: str) -> Path:
        if report_format is ReportFormat.EXCEL:
            return export_to_excel(result.rows, result.columns, output_path, sheet_title=title[:31])
        if report_format is ReportFormat.PDF:
            fonts_dir = self._config.paths.assets_dir / "fonts"
            return export_to_pdf(result.rows, result.columns, output_path, title=title, fonts_dir=fonts_dir)
        if report_format is ReportFormat.CSV:
            return export_to_csv(result.rows, result.columns, output_path)
        raise ValueError(f"Unsupported report format: {report_format!r}")

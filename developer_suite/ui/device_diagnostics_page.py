"""Device Diagnostics page: safe, read-only network investigation of an
attendance device whose protocol/connector is not yet known.

Built for the DELI ES172 investigation (see
``tools/diagnostics/deli_es172_diagnose.py``'s own module docstring for
the full rationale and safety boundary), but generic to any device --
the target IP is free-form, never hard-coded. Reuses the exact same
diagnostic logic the Attendance Client's Devices page exposes via its
own "تشخيص شبكة الجهاز" button (``ui.devices.NetworkDiagnosticDialog``);
this page exists so the same investigation can be run from the
Developer Suite -- e.g. while validating a new device before it ships
to a customer -- without installing Python separately either.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from developer_suite.config import DeveloperSuiteConfig
from tools.diagnostics import deli_es172_diagnose as network_diagnostic


def _format_diagnostic_summary(report: dict[str, Any]) -> str:
    """Plain-language Arabic summary of a :func:`~tools.diagnostics.deli_es172_diagnose.diagnose` report.

    Mirrors ``ui.devices._format_network_diagnostic_summary`` (kept
    separate rather than imported, since the Attendance Client and the
    Developer Suite are independently packaged applications with no
    runtime dependency on each other).
    """
    lines: list[str] = [
        f"عنوان الجهاز: {report['target_ip']}",
        "استجابة ping: " + ("نعم، الجهاز متصل بالشبكة" if report["ping"]["reachable"] else "لا استجابة"),
        "",
    ]
    for entry in report["ports"]:
        if not entry["open"]:
            lines.append(f"المنفذ {entry['port']}: مغلق أو غير متاح")
            continue
        lines.append(f"المنفذ {entry['port']}: مفتوح")
        banner_bytes = entry.get("unsolicited_banner_bytes") or 0
        if banner_bytes:
            lines.append(f"  - أرسل الجهاز بيانات فور الاتصال ({banner_bytes} بايت)")
        if any(probe.get("looks_like_http") for probe in entry.get("http_probes", [])):
            lines.append("  - يستجيب كخادم HTTP عادي")
        websocket_probe = entry.get("websocket_probe")
        if websocket_probe and websocket_probe.get("is_101_switching_protocols"):
            lines.append("  - يقبل ترقية الاتصال إلى WebSocket")
        tls = entry.get("tls")
        if tls and tls.get("tls_handshake_ok"):
            lines.append("  - يدعم تشفير TLS على هذا المنفذ")
        dev = entry.get("dev_interface")
        if dev and not dev.get("error"):
            lines.append(
                "  - يحتوي على واجهة ويب للتطوير/الاختبار: "
                f"{len(dev.get('links', []))} روابط، "
                f"{len(dev.get('script_srcs', []))} ملفات JavaScript، "
                f"{len(dev.get('fetched_resources', [])) + len(dev.get('followed_links', []))} مورد تم تحميله لفحصه"
            )
            if dev.get("mentions_port_5005"):
                lines.append("  - الصفحة تشير إلى المنفذ 5005")
        unk = entry.get("unknown_protocol_investigation")
        if unk:
            total_bytes = unk.get("extended_passive_listen", {}).get("total_bytes", 0)
            probe_replies = sum(1 for p in unk.get("generic_probes", []) if p.get("rx_bytes"))
            lines.append(
                "  - بروتوكول غير معروف: تم الاستماع بشكل موسّع "
                f"({total_bytes} بايت) وإرسال {len(unk.get('generic_probes', []))} فحص عام "
                f"(استجاب الجهاز في {probe_replies} منها)"
            )
        lines.append("")
    return "\n".join(lines).strip()


class DeviceDiagnosticsPage(QWidget):
    """Safe, protocol-agnostic network diagnostic for a device with no connector yet."""

    def __init__(self, config: DeveloperSuiteConfig, *, parent: QWidget | None = None) -> None:
        """Build the page.

        Args:
            config: Supplies the writable directory diagnostic reports
                are saved into (``config.paths.data_dir / "diagnostics"``).
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._config = config
        self._last_output_dir: Path | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)

        title = QLabel("تشخيص شبكة الجهاز", self)
        title_font = title.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        outer.addWidget(title)

        intro = QLabel(
            "لفحص جهاز غير مدعوم حالياً على الشبكة (مثل بعض أجهزة DELI): أدخل عنوان IP "
            "الخاص به كما يظهر على شاشة الجهاز نفسه، ثم اضغط \"بدء الفحص\". هذا الفحص آمن "
            "تماماً؛ يتحقق فقط من استجابة الجهاز على الشبكة ولا يرسل أي كلمة مرور أو مفتاح "
            "اتصال أو أمر تسجيل أو تعديل.",
            self,
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        form = QFormLayout()
        self.ip_edit = QLineEdit(self)
        self.ip_edit.setText(network_diagnostic.DEFAULT_TARGET_IP)
        form.addRow("عنوان الجهاز (IP)", self.ip_edit)
        outer.addLayout(form)

        button_row = QHBoxLayout()
        self.run_button = QPushButton("بدء الفحص", self)
        self.run_button.clicked.connect(self._on_run_clicked)
        button_row.addWidget(self.run_button)

        self.open_folder_button = QPushButton("فتح مجلد التقرير", self)
        self.open_folder_button.setEnabled(False)
        self.open_folder_button.clicked.connect(self._on_open_folder_clicked)
        button_row.addWidget(self.open_folder_button)
        button_row.addStretch(1)
        outer.addLayout(button_row)

        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)

        self.result_view = QTextEdit(self)
        self.result_view.setReadOnly(True)
        self.result_view.setPlaceholderText("ستظهر نتيجة الفحص هنا بعد التشغيل...")
        outer.addWidget(self.result_view, stretch=1)

    def _on_run_clicked(self) -> None:
        """Run the diagnostic against the entered IP and display + save the result."""
        ip = self.ip_edit.text().strip()
        if not ip:
            self.status_label.setText("الرجاء إدخال عنوان IP للجهاز.")
            return
        self.run_button.setEnabled(False)
        self.status_label.setText("جارٍ الفحص ...")
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
        try:
            report = network_diagnostic.diagnose(ip)
            output_dir = self._config.paths.data_dir / "diagnostics"
            json_path, txt_path = network_diagnostic._write_reports(report, output_dir=output_dir)
            self._last_output_dir = output_dir
            self.open_folder_button.setEnabled(True)
            self.status_label.setText(
                f"تم حفظ تقرير كامل قابل للإرسال:\n{json_path.name}\n{txt_path.name}"
            )
            self.result_view.setPlainText(_format_diagnostic_summary(report))
        finally:
            self.run_button.setEnabled(True)

    def _on_open_folder_clicked(self) -> None:
        """Open the folder containing the saved diagnostic reports."""
        if self._last_output_dir is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_output_dir)))

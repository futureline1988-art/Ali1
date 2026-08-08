"""On-device face-enrollment workflow dialog, plus its supporting device picker.

This project's ZKTeco integration (``pyzk``) has no protocol command to
remotely trigger face enrollment or upload a face template from this
PC — verified directly against the installed library (see
:mod:`devices.zkteco_device`'s own module docstring). This dialog is
built around that fact rather than pretending otherwise: it can only
push the employee's record to the device, ask a human to walk the
employee to the physical device and enroll there, and then check
whether the device's own enrolled-face count went up — never capture,
display, or upload a face image itself (see
:meth:`~services.device_service.DeviceService.confirm_face_enrollment`'s
own docstring for exactly what "verify" means here).

Polling happens on a background :class:`~PySide6.QtCore.QThread` so
the dialog stays responsive while waiting — the controller calls it
makes are safe to run off the GUI thread because
:class:`~database.database.Database` uses a thread-local
``scoped_session`` over a ``check_same_thread=False`` SQLite
connection (see ``database/database.py``).
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.widgets import make_heading_label, make_secondary_label, run_blocking_operation

#: How often the background worker asks the device for its current face count.
_POLL_INTERVAL_SECONDS = 3.0
#: After this long with no new enrollment detected, the dialog stops
#: auto-polling and tells the operator to check manually or cancel —
#: never fails silently, and never keeps a background thread running
#: forever if the window is simply left open.
_ENROLLMENT_TIMEOUT_SECONDS = 120.0


class _FaceEnrollmentPollWorker(QThread):
    """Repeatedly polls the device's face count without blocking the UI thread."""

    progress = Signal(object)  # int | None

    def __init__(
        self, *, poll: Callable[[], int | None], interval_seconds: float, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._poll = poll
        self._interval_seconds = interval_seconds
        self._stop_requested = False

    def request_stop(self) -> None:
        """Ask the loop to exit before its next poll — safe to call from the GUI thread."""
        self._stop_requested = True

    def run(self) -> None:  # noqa: D102 - QThread override
        while not self._stop_requested:
            count = self._poll()
            if self._stop_requested:
                return
            self.progress.emit(count)
            self.msleep(int(self._interval_seconds * 1000))


class SelectDeviceDialog(QDialog):
    """A focused dialog for picking which active device to use for an operation."""

    def __init__(self, *, devices: list[dict[str, Any]], parent: QWidget | None = None) -> None:
        """Build the dialog.

        Args:
            devices: The candidate devices (already filtered by the
                caller, e.g. to only face-capable-protocol devices).
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("اختيار الجهاز")
        self.setMinimumWidth(360)

        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.device_combo = QComboBox(self)
        for device in devices:
            label = f"{device['name']} ({device.get('protocol_label_ar', '')})"
            self.device_combo.addItem(label, userData=device["id"])
        form.addRow("الجهاز", self.device_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.button(QDialogButtonBox.Ok).setText("متابعة")
        buttons.button(QDialogButtonBox.Cancel).setText("إلغاء")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def selected_device_id(self) -> int | None:
        """The chosen device's id, or ``None`` if the list was empty."""
        return self.device_combo.currentData()


class FaceEnrollmentDialog(QDialog):
    """Guides a non-blocking on-device face-enrollment attempt for one employee.

    Construct only after
    :meth:`~controllers.device_controller.DeviceController.begin_face_enrollment`
    has already returned ``outcome == "started"`` — this dialog only
    ever handles the waiting/confirming phase.
    """

    def __init__(
        self,
        *,
        controller: Any,
        device_id: int,
        employee_id: int,
        enrollment_session: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        """Build the dialog and start polling immediately.

        Args:
            controller: The
                :class:`~controllers.device_controller.DeviceController`
                driving this attempt.
            device_id: The device enrollment was started on.
            employee_id: The employee enrollment was started for.
            enrollment_session: The ``session`` dict
                :meth:`~controllers.device_controller.DeviceController.begin_face_enrollment`
                returned.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("تسجيل بصمة الوجه")
        self.setMinimumWidth(420)
        self.setModal(False)

        self._controller = controller
        self._device_id = device_id
        self._employee_id = employee_id
        self._session = enrollment_session
        self._before_count = enrollment_session.get("before_face_count")
        self._elapsed_seconds = 0.0
        self._final_result: dict[str, Any] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)

        instructions = QLabel(
            "يرجى من الموظف الوقوف أمام جهاز البصمة وإكمال تسجيل الوجه من قائمة "
            "الجهاز نفسه — هذا التطبيق لا يستطيع بدء عملية التسجيل عن بُعد.",
            self,
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        self.status_label = make_secondary_label("جارٍ الانتظار... لم يتم اكتشاف تسجيل جديد بعد.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons_row = QHBoxLayout()
        self.check_now_button = QPushButton("تحقق الآن", self)
        self.check_now_button.clicked.connect(self._check_now)
        buttons_row.addWidget(self.check_now_button)

        self.confirm_button = QPushButton("تأكيد أن التسجيل يخص هذا الموظف", self)
        self.confirm_button.setEnabled(False)
        self.confirm_button.clicked.connect(self._on_confirm_clicked)
        buttons_row.addWidget(self.confirm_button)
        layout.addLayout(buttons_row)

        self.cancel_button = QPushButton("إلغاء", self)
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        layout.addWidget(self.cancel_button)

        self._timeout_timer = QTimer(self)
        self._timeout_timer.setInterval(1000)
        self._timeout_timer.timeout.connect(self._on_tick)
        self._timeout_timer.start()

        self._worker = _FaceEnrollmentPollWorker(
            poll=lambda: self._controller.check_face_enrollment_progress(self._device_id),
            interval_seconds=_POLL_INTERVAL_SECONDS,
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.start()

    def final_result(self) -> dict[str, Any] | None:
        """The last :class:`~services.device_service.FaceEnrollmentResult` dict, if confirmed."""
        return self._final_result

    def _on_tick(self) -> None:
        self._elapsed_seconds += 1.0
        if self._elapsed_seconds >= _ENROLLMENT_TIMEOUT_SECONDS and not self.confirm_button.isEnabled():
            self.status_label.setText(
                "انتهت مهلة الانتظار دون اكتشاف تسجيل جديد. يمكنك الضغط على "
                "‏«تحقق الآن» أو الإلغاء."
            )
            self._timeout_timer.stop()

    def _on_progress(self, count: object) -> None:
        if count is not None and self._before_count is not None and count > self._before_count:
            self.status_label.setText(
                "تم اكتشاف تسجيل جديد على الجهاز. يرجى التأكيد أنه يخص هذا الموظف."
            )
            self.confirm_button.setEnabled(True)
        elif count is None:
            self.status_label.setText("تعذر الاتصال بالجهاز أثناء التحقق. ستتم إعادة المحاولة تلقائيًا.")
        elif not self.confirm_button.isEnabled():
            self.status_label.setText("جارٍ الانتظار... لم يتم اكتشاف تسجيل جديد بعد.")

    def _check_now(self) -> None:
        count = self._controller.check_face_enrollment_progress(self._device_id)
        self._on_progress(count)

    def _on_confirm_clicked(self) -> None:
        self._stop_worker()
        self._final_result = self._controller.confirm_face_enrollment(
            device_id=self._device_id,
            employee_id=self._employee_id,
            enrollment_session=self._session,
            operator_confirmed=True,
        )
        self.accept()

    def _on_cancel_clicked(self) -> None:
        self._stop_worker()
        self._controller.cancel_face_enrollment(self._employee_id)
        self.reject()

    def _stop_worker(self) -> None:
        self._timeout_timer.stop()
        self._worker.request_stop()
        self._worker.wait(2000)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Stop the background poller if the window is closed via the title bar."""
        self._stop_worker()
        super().closeEvent(event)


class BiometricStatusDialog(QDialog):
    """Shows one employee's biometric enrollment status, with refresh/reset actions.

    Displays exactly what
    :meth:`~controllers.device_controller.DeviceController.get_employee_biometric_status`/
    :meth:`~controllers.device_controller.DeviceController.refresh_employee_biometric_status`
    return: fingerprint count and card assignment (both genuinely
    device-verified, refreshable on demand), and face-enrolled state
    (only ever set by a confirmed
    :class:`FaceEnrollmentDialog` attempt or cleared by "إعادة ضبط
    محليًا" — see
    :meth:`~services.device_service.DeviceService.reset_face_enrollment_status`'s
    own docstring for why that reset never touches the device itself).
    """

    def __init__(
        self,
        *,
        controller: Any,
        employee_id: int,
        status: dict[str, Any],
        devices: list[dict[str, Any]],
        parent: QWidget | None = None,
    ) -> None:
        """Build the dialog from an already-fetched ``status`` dict.

        Args:
            controller: The
                :class:`~controllers.device_controller.DeviceController`
                to call for refresh/reset actions.
            employee_id: The employee this status belongs to.
            status: An employee biometric-status dict, as returned by
                :meth:`~controllers.device_controller.DeviceController.get_employee_biometric_status`.
            devices: This company's devices (for the "device" field's
                display name, and to offer as refresh targets).
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("الحالة البيومترية")
        self.setMinimumWidth(380)

        self._controller = controller
        self._employee_id = employee_id
        self._devices = devices
        self._device_names_by_id = {device["id"]: device["name"] for device in devices}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(make_heading_label("الحالة البيومترية"))

        self._form = QFormLayout()
        layout.addLayout(self._form)
        self._face_value_label = make_secondary_label("")
        self._device_value_label = make_secondary_label("")
        self._fingerprint_value_label = make_secondary_label("")
        self._card_value_label = make_secondary_label("")
        self._palm_value_label = make_secondary_label("")
        self._sync_value_label = make_secondary_label("")
        self._form.addRow("بصمة الوجه", self._face_value_label)
        self._form.addRow("الجهاز", self._device_value_label)
        self._form.addRow("عدد بصمات الإصبع", self._fingerprint_value_label)
        self._form.addRow("بطاقة مخصصة", self._card_value_label)
        self._form.addRow("بصمة الكف", self._palm_value_label)
        self._form.addRow("آخر مزامنة", self._sync_value_label)
        self._apply_status(status)

        buttons_row = QHBoxLayout()
        self.refresh_button = QPushButton("تحديث من الجهاز", self)
        self.refresh_button.setEnabled(bool(devices))
        self.refresh_button.clicked.connect(self._on_refresh_clicked)
        buttons_row.addWidget(self.refresh_button)

        self.reset_button = QPushButton("إعادة ضبط بصمة الوجه محليًا", self)
        self.reset_button.clicked.connect(self._on_reset_clicked)
        buttons_row.addWidget(self.reset_button)
        layout.addLayout(buttons_row)

        close_button = QPushButton("إغلاق", self)
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

    def _apply_status(self, status: dict[str, Any]) -> None:
        self._face_value_label.setText("مُسجَّلة" if status.get("face_enrolled") else "غير مُسجَّلة")
        device_id = status.get("face_enrolled_device_id")
        self._device_value_label.setText(
            self._device_names_by_id.get(device_id, "—") if status.get("face_enrolled") else "—"
        )
        self._fingerprint_value_label.setText(str(status.get("fingerprint_count") or 0))
        self._card_value_label.setText("نعم" if status.get("card_assigned") else "لا")
        self._palm_value_label.setText("مُسجَّلة" if status.get("palm_registered") else "غير مُسجَّلة")
        self._sync_value_label.setText(status.get("biometric_last_synced_at") or "—")

    def _on_refresh_clicked(self) -> None:
        dialog = SelectDeviceDialog(devices=self._devices, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        device_id = dialog.selected_device_id()
        if device_id is None:
            return
        status = run_blocking_operation(
            self,
            "جارٍ تحديث الحالة البيومترية من الجهاز...",
            lambda: self._controller.refresh_employee_biometric_status(
                device_id=device_id, employee_id=self._employee_id
            ),
        )
        if status is not None:
            self._apply_status(status)

    def _on_reset_clicked(self) -> None:
        status = self._controller.reset_face_enrollment_status(self._employee_id)
        if status is not None:
            self._apply_status(status)

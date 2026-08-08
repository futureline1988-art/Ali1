"""On-device DELI ES172 biometric-enrollment workflow dialog.

Unlike this project's ZKTeco integration (see
:mod:`ui.face_enrollment_dialog`'s own module docstring for why that
one can only guess at success via a device-wide face-count diff), the
official DELI Offline SDK Protocol gives a real job/template lifecycle:
``BeginEnrollFace``/``BeginEnrollFp``/``BeginEnrollPalm`` starts a job,
``QueryJobStatus`` reports ``pending``/``succeeded``/``failed``, and a
succeeded job hands back the actual captured template -- which this
dialog then explicitly saves to the employee via ``SetUserInfo``
(:meth:`~controllers.device_controller.DeviceController.confirm_deli_enrollment`).
No operator "was this really them" confirmation step is needed: the
device itself proves which job the template came from.

Polling happens on a background :class:`~PySide6.QtCore.QThread`,
exactly like :mod:`ui.face_enrollment_dialog`'s poller, so the dialog
stays responsive while waiting.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget, QDialog

from ui.widgets import make_secondary_label

#: How often the background worker asks the device for this job's current state.
_POLL_INTERVAL_SECONDS = 2.0

_KIND_LABELS_AR = {
    "face": "بصمة الوجه",
    "fp": "بصمة الإصبع",
    "palm": "بصمة الكف",
}


class _DeliEnrollmentPollWorker(QThread):
    """Repeatedly polls one DELI enrollment job's status without blocking the UI thread."""

    progress = Signal(object)  # dict | None

    def __init__(
        self,
        *,
        poll: Callable[[], dict[str, Any] | None],
        interval_seconds: float,
        parent: QWidget | None = None,
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
            result = self._poll()
            if self._stop_requested:
                return
            self.progress.emit(result)
            if result is not None and result.get("outcome") != "pending":
                return
            self.msleep(int(self._interval_seconds * 1000))


class DeliEnrollmentDialog(QDialog):
    """Guides a non-blocking DELI ES172 on-device biometric-enrollment attempt.

    Construct only after
    :meth:`~controllers.device_controller.DeviceController.begin_deli_enrollment`
    has already returned ``outcome == "started"`` — this dialog only
    ever handles the waiting/confirming/cancelling phase.
    """

    def __init__(
        self,
        *,
        controller: Any,
        device_id: int,
        employee_id: int,
        employee_name: str,
        begin_result: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        """Build the dialog and start polling immediately.

        Args:
            controller: The
                :class:`~controllers.device_controller.DeviceController`
                driving this attempt.
            device_id: The device enrollment was started on.
            employee_id: The employee enrollment was started for.
            employee_name: Shown in the window title for context.
            begin_result: The dict
                :meth:`~controllers.device_controller.DeviceController.begin_deli_enrollment`
                returned (``outcome == "started"``, carrying the new job).
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._job: dict[str, Any] = begin_result["job"]
        kind_label = _KIND_LABELS_AR.get(self._job["kind"], self._job["kind"])
        self.setWindowTitle(f"تسجيل {kind_label} - {employee_name}")
        self.setMinimumWidth(420)
        self.setModal(False)

        self._controller = controller
        self._device_id = device_id
        self._employee_id = employee_id
        self._final_result: dict[str, Any] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)

        instructions = QLabel(
            f"يرجى من الموظف الوقوف أمام الجهاز الآن لإكمال تسجيل {kind_label}.", self
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        self.status_label = make_secondary_label(begin_result.get("message_ar", ""))
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons_row = QHBoxLayout()
        self.confirm_button = QPushButton("حفظ للموظف", self)
        self.confirm_button.setEnabled(False)
        self.confirm_button.clicked.connect(self._on_confirm_clicked)
        buttons_row.addWidget(self.confirm_button)
        layout.addLayout(buttons_row)

        self.cancel_button = QPushButton("إلغاء", self)
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        layout.addWidget(self.cancel_button)

        self._worker = _DeliEnrollmentPollWorker(
            poll=lambda: self._controller.poll_deli_enrollment_job(
                device_id=self._device_id, job=self._job
            ),
            interval_seconds=_POLL_INTERVAL_SECONDS,
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.start()

    def final_result(self) -> dict[str, Any] | None:
        """The last :class:`~services.device_service.DeliEnrollmentResult` dict, if confirmed."""
        return self._final_result

    def _on_progress(self, result: object) -> None:
        if result is None:
            self.status_label.setText(
                "تعذر الاتصال بالجهاز أثناء التحقق من حالة التسجيل. ستتم إعادة المحاولة تلقائيًا."
            )
            return
        if result.get("job") is not None:
            self._job = result["job"]
        self.status_label.setText(result.get("message_ar", ""))
        outcome = result.get("outcome")
        if outcome == "succeeded":
            self.confirm_button.setEnabled(True)
        elif outcome in ("failed", "disconnected"):
            self.cancel_button.setText("إغلاق")

    def _on_confirm_clicked(self) -> None:
        self._stop_worker()
        self._final_result = self._controller.confirm_deli_enrollment(
            device_id=self._device_id, employee_id=self._employee_id, job=self._job
        )
        self.accept()

    def _on_cancel_clicked(self) -> None:
        self._stop_worker()
        if self._final_result is None:
            self._controller.cancel_deli_enrollment(device_id=self._device_id, job=self._job)
        self.reject()

    def _stop_worker(self) -> None:
        self._worker.request_stop()
        self._worker.wait(2000)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Stop the background poller if the window is closed via the title bar."""
        self._stop_worker()
        super().closeEvent(event)

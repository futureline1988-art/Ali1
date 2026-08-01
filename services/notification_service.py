"""In-app notification service: an event bus for the UI's notification bell.

Not a channel for external delivery (email/SMS) — this is purely
in-process: any service can call :meth:`NotificationCenter.post` to
surface something to the user (a device going offline, a leave request
awaiting approval, a completed backup), and the main window subscribes
to :attr:`NotificationCenter.notification_posted` to render it (a toast,
a badge count on a bell icon, an entry in a notification panel).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from PySide6.QtCore import QObject, Signal

from utils.logger import get_logger


class NotificationLevel(str, Enum):
    """Visual severity of a notification, driving its icon/color in the UI."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class Notification:
    """A single in-app notification.

    Attributes:
        title: Short headline (e.g. ``"جهاز غير متصل"``).
        message: Full message body.
        level: Visual severity.
        created_at: When the notification was posted (UTC).
        company_id: The company this notification relates to, if any.
        is_read: Whether the user has acknowledged it. Mutable so the
            UI can mark a notification read in place.
    """

    title: str
    message: str
    level: NotificationLevel
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    company_id: int | None = None
    is_read: bool = False


class NotificationCenter(QObject):
    """Thread-safe in-process notification bus.

    Any thread may call :meth:`post` (a device polling worker running
    in the background is a typical caller); the emitted
    :attr:`notification_posted` signal is delivered on the Qt event
    loop as usual, so UI code connected to it does not need to worry
    about cross-thread access.
    """

    notification_posted = Signal(object)
    """Emitted with the new :class:`Notification` every time :meth:`post` is called."""

    def __init__(self, *, history_limit: int = 200) -> None:
        """Create a notification center.

        Args:
            history_limit: Maximum number of past notifications kept in
                memory; oldest are dropped beyond this.
        """
        super().__init__()
        self._lock = threading.Lock()
        self._history: list[Notification] = []
        self._history_limit = history_limit
        self._logger = get_logger(component="notifications")

    def post(
        self,
        title: str,
        message: str,
        *,
        level: NotificationLevel = NotificationLevel.INFO,
        company_id: int | None = None,
    ) -> Notification:
        """Post a new notification.

        Args:
            title: Short headline.
            message: Full message body.
            level: Visual severity.
            company_id: The company this notification relates to, if
                any.

        Returns:
            The created :class:`Notification`.
        """
        notification = Notification(
            title=title, message=message, level=level, company_id=company_id
        )
        with self._lock:
            self._history.append(notification)
            if len(self._history) > self._history_limit:
                self._history = self._history[-self._history_limit :]

        self._logger.bind(level=level.value, company_id=company_id).info(
            "{title}: {message}", title=title, message=message
        )
        self.notification_posted.emit(notification)
        return notification

    def recent(
        self, *, limit: int = 50, company_id: int | None = None, unread_only: bool = False
    ) -> list[Notification]:
        """List recent notifications, most recent first.

        Args:
            limit: Maximum number to return.
            company_id: Restrict to one company's notifications, if
                given.
            unread_only: Restrict to notifications not yet marked read.

        Returns:
            Matching notifications, most recent first.
        """
        with self._lock:
            snapshot = list(self._history)
        if company_id is not None:
            snapshot = [n for n in snapshot if n.company_id == company_id]
        if unread_only:
            snapshot = [n for n in snapshot if not n.is_read]
        return list(reversed(snapshot))[:limit]

    def unread_count(self, *, company_id: int | None = None) -> int:
        """Count unread notifications.

        Args:
            company_id: Restrict to one company's notifications, if
                given.

        Returns:
            The unread count.
        """
        return len(self.recent(limit=self._history_limit, company_id=company_id, unread_only=True))

    def mark_all_read(self, *, company_id: int | None = None) -> None:
        """Mark every matching notification as read.

        Args:
            company_id: Restrict to one company's notifications, if
                given; ``None`` marks every notification read.
        """
        with self._lock:
            for notification in self._history:
                if company_id is None or notification.company_id == company_id:
                    notification.is_read = True


_notification_center: NotificationCenter | None = None
_center_lock = threading.Lock()


def get_notification_center() -> NotificationCenter:
    """Return the process-wide :class:`NotificationCenter` singleton."""
    global _notification_center
    if _notification_center is None:
        with _center_lock:
            if _notification_center is None:
                _notification_center = NotificationCenter()
    return _notification_center

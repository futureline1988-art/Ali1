"""Base controller: session-per-operation execution with UI-friendly error reporting.

Every controller runs each user-initiated action inside its own fresh
:func:`~database.database.session_scope` — a controller method is one
unit of work, matching how a single UI action (click Save, click
Delete) should correspond to one commit. Any exception raised by the
service layer is caught here, logged, and reported via
:attr:`BaseController.operation_failed` instead of propagating into
Qt's event loop, which is not equipped to handle Python exceptions
raised from a slot.
"""

from __future__ import annotations

from typing import Callable, TypeVar

from PySide6.QtCore import QObject, Signal
from sqlalchemy.orm import Session

from database.database import session_scope
from utils.logger import get_logger

ResultT = TypeVar("ResultT")


class BaseController(QObject):
    """Shared session-per-operation execution helper.

    Concrete controllers call :meth:`_run` with a function that takes
    the open session, does its work through a service, and returns
    already session-independent data (a dict, a list of dicts, a
    primitive) — never a bare ORM object, which would risk a
    ``DetachedInstanceError`` the moment the UI touches an unloaded
    relationship after the session closes.

    Attributes:
        company_id: The company every operation through this
            controller is scoped to.
        actor_user_id: The current user, for audit attribution.
    """

    operation_failed = Signal(str)
    """Emitted with a user-facing error message whenever a controller
    operation raises."""

    def __init__(self, *, company_id: int, actor_user_id: int | None = None) -> None:
        """Create a controller.

        Args:
            company_id: The company to scope every operation to.
            actor_user_id: The current user's id, for audit
                attribution.
        """
        super().__init__()
        self.company_id = company_id
        self.actor_user_id = actor_user_id
        self._logger = get_logger(company_id=company_id, user_id=actor_user_id)

    def _run(self, func: Callable[[Session], ResultT]) -> ResultT | None:
        """Execute ``func`` inside a fresh session, reporting failures via signal.

        Args:
            func: A callable taking the open :class:`~sqlalchemy.orm.Session`
                and returning a plain, session-independent result.

        Returns:
            ``func``'s return value, or ``None`` if it raised.
        """
        try:
            with session_scope() as session:
                return func(session)
        except Exception as exc:  # noqa: BLE001 - this IS the UI error boundary
            self._logger.error("Controller operation failed: {error}", error=str(exc))
            self.operation_failed.emit(str(exc))
            return None

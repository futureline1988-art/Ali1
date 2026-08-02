"""Base controller: session-per-operation execution with UI-friendly error reporting.

Every controller runs each user-initiated action inside its own fresh
:func:`~database.database.session_scope` — a controller method is one
unit of work, matching how a single UI action (click Save, click
Delete) should correspond to one commit. Any exception raised by the
service layer is caught here, logged, and reported via
:attr:`BaseController.operation_failed` instead of propagating into
Qt's event loop, which is not equipped to handle Python exceptions
raised from a slot.

This module is also the runtime RBAC enforcement point: every
controller is constructed with the current user's granted
:attr:`~models.permission.Permission.code` set (resolved once, at
login, from their :class:`~models.role.Role` — see
``controllers/auth_controller.py``), and any method decorated with
:func:`requires_permission` refuses to run — without touching the
database at all — unless at least one of the required codes is
present. The permission *catalog* and the *editor UI* for granting
codes to a role predate this module; this is what makes those grants
actually mean something instead of being purely decorative.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, TypeVar

from PySide6.QtCore import QObject, Signal
from sqlalchemy.orm import Session

from database.database import session_scope
from utils.logger import get_logger

ResultT = TypeVar("ResultT")

_ACCESS_DENIED_MESSAGE = "ليس لديك صلاحية للقيام بهذا الإجراء."


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
        permission_codes: The current user's granted permission codes
            (from their role), consulted by :func:`requires_permission`.
            Defaults to an empty set — deny by default — so a
            controller nobody remembered to pass real codes to is
            locked down rather than silently wide open.
    """

    operation_failed = Signal(str)
    """Emitted with a user-facing error message whenever a controller
    operation raises, or a permission check fails."""

    def __init__(
        self,
        *,
        company_id: int,
        actor_user_id: int | None = None,
        permission_codes: frozenset[str] = frozenset(),
    ) -> None:
        """Create a controller.

        Args:
            company_id: The company to scope every operation to.
            actor_user_id: The current user's id, for audit
                attribution.
            permission_codes: The current user's granted permission
                codes, for :func:`requires_permission` checks.
        """
        super().__init__()
        self.company_id = company_id
        self.actor_user_id = actor_user_id
        self.permission_codes = permission_codes
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


def requires_permission(
    *codes: str, default: Any = None
) -> Callable[[Callable[..., ResultT]], Callable[..., ResultT]]:
    """Gate a controller method behind one or more permission codes.

    Apply directly above a controller method. The wrapped method runs
    only if the controller instance's :attr:`BaseController.permission_codes`
    contains at least one of ``codes``; otherwise it emits
    :attr:`BaseController.operation_failed` with a user-facing message
    and returns ``default`` immediately, without opening a database
    session or calling the wrapped method at all.

    ``default`` matters because the decorator sits *outside* the method
    body: a list-returning method's own ``self._run(do_list) or []``
    fallback, or a bool-returning method's own ``bool(result)``, never
    executes on a denial, since the wrapped method is never called at
    all. Every list-returning method in this codebase must pass
    ``default=[]`` and every bool-returning one ``default=False`` to
    match its declared return type and what its callers already assume
    (e.g. ``ui/table_page.py``'s ``populate()`` calling ``len()`` on
    whatever a list method returns) - the plain ``None`` default is only
    correct for the dict-or-None-returning methods, which already treat
    ``None`` as "the operation failed" at every call site.

    Args:
        *codes: One or more :attr:`~models.permission.Permission.code`
            values; any single match grants access (e.g. a role with
            either ``"leave.manage"`` or ``"leave.view"``, for a method
            that's safe for either).
        default: The value to return when access is denied; must match
            the wrapped method's own failure-path return value.

    Returns:
        A decorator to apply to a :class:`BaseController` method.
    """

    def decorator(method: Callable[..., ResultT]) -> Callable[..., ResultT]:
        @functools.wraps(method)
        def wrapper(self: BaseController, *args: Any, **kwargs: Any) -> ResultT:
            if not set(codes) & self.permission_codes:
                self._logger.warning(
                    "Permission denied: user {user} lacks any of {codes} for {method}",
                    user=self.actor_user_id,
                    codes=codes,
                    method=method.__qualname__,
                )
                self.operation_failed.emit(_ACCESS_DENIED_MESSAGE)
                return default
            return method(self, *args, **kwargs)

        return wrapper

    return decorator

"""Holiday controller: bridges the holidays screen to ``HolidayService``."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from sqlalchemy.orm import Session

from controllers.base_controller import BaseController, requires_permission
from repositories.holiday_repository import HolidayRepository
from services.holiday_service import HolidayService


class HolidayController(BaseController):
    """Controller for the holidays management screen."""

    holidays_changed = Signal()
    """Emitted after any successful create/update/delete."""

    @requires_permission("holidays.manage")
    def create_holiday(self, **fields: Any) -> dict[str, Any] | None:
        """Create a new holiday.

        Args mirror :meth:`~services.holiday_service.HolidayService.create_holiday`.

        Returns:
            The new holiday's data as a dict, or ``None`` on failure.
        """

        def do_create(session: Session) -> dict[str, Any]:
            service = HolidayService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            holiday = service.create_holiday(**fields)
            return holiday.to_dict()

        result = self._run(do_create)
        if result is not None:
            self.holidays_changed.emit()
        return result

    @requires_permission("holidays.manage")
    def update_holiday(self, holiday_id: int, **fields: Any) -> dict[str, Any] | None:
        """Update a holiday's editable fields.

        Args:
            holiday_id: The holiday to update.
            **fields: See
                :meth:`~services.holiday_service.HolidayService.update_holiday`.

        Returns:
            The updated holiday's data as a dict, or ``None`` on
            failure.
        """

        def do_update(session: Session) -> dict[str, Any]:
            repo = HolidayRepository(session, company_id=self.company_id)
            holiday = repo.get_by_id(holiday_id)
            if holiday is None:
                raise ValueError(f"Holiday {holiday_id!r} was not found.")
            service = HolidayService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            updated = service.update_holiday(holiday, **fields)
            return updated.to_dict()

        result = self._run(do_update)
        if result is not None:
            self.holidays_changed.emit()
        return result

    @requires_permission("holidays.manage", default=False)
    def delete_holiday(self, holiday_id: int) -> bool:
        """Soft-delete a holiday.

        Args:
            holiday_id: The holiday to delete.

        Returns:
            ``True`` on success, ``False`` on failure.
        """

        def do_delete(session: Session) -> bool:
            repo = HolidayRepository(session, company_id=self.company_id)
            holiday = repo.get_by_id(holiday_id)
            if holiday is None:
                raise ValueError(f"Holiday {holiday_id!r} was not found.")
            service = HolidayService(
                session, company_id=self.company_id, actor_user_id=self.actor_user_id
            )
            service.delete_holiday(holiday)
            return True

        result = self._run(do_delete)
        if result:
            self.holidays_changed.emit()
        return bool(result)

    @requires_permission("holidays.view", "holidays.manage", default=[])
    def list_holidays(self) -> list[dict[str, Any]]:
        """List every holiday defined for this company.

        Returns:
            Holidays' data as dicts, ordered by id; an empty list on
            failure.
        """

        def do_list(session: Session) -> list[dict[str, Any]]:
            service = HolidayService(session, company_id=self.company_id)
            return [holiday.to_dict() for holiday in service.list_all()]

        return self._run(do_list) or []

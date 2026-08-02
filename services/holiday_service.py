"""Holiday calendar management service.

Plain CRUD over :class:`~models.holiday.Holiday`, following the same
shape as :class:`~services.department_service.DepartmentService`. The
only domain rule enforced here beyond field validation is the
duplicate check backing the model's ``(company_id, holiday_date, name)``
unique constraint — checked in Python rather than only relying on the
database's constraint violation, so callers get a clear
:class:`HolidayValidationError` instead of a raw
:class:`~sqlalchemy.exc.IntegrityError`.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from models.audit_log import AuditLog
from models.enums import AuditAction
from models.holiday import Holiday
from repositories.audit_log_repository import AuditLogRepository
from repositories.holiday_repository import HolidayRepository
from utils.validators import is_within_length

_UPDATABLE_FIELDS = frozenset(
    {"name", "holiday_date", "is_recurring_annually", "is_active", "description"}
)


class HolidayValidationError(Exception):
    """Raised when holiday input fails validation."""


class HolidayService:
    """Holiday calendar operations scoped to one company.

    Attributes:
        session: The active database session.
        company_id: The company this service operates within.
        actor_user_id: The user performing these operations, recorded
            on every audit log entry.
    """

    def __init__(
        self, session: Session, *, company_id: int, actor_user_id: int | None = None
    ) -> None:
        """Create a holiday service bound to one session and company.

        Args:
            session: The active database session.
            company_id: The company to operate within.
            actor_user_id: The acting user's id, for audit attribution.
        """
        self.session = session
        self.company_id = company_id
        self.actor_user_id = actor_user_id
        self.holiday_repo = HolidayRepository(session, company_id=company_id)
        self.audit_repo = AuditLogRepository(session)

    def create_holiday(
        self,
        *,
        name: str,
        holiday_date: date,
        is_recurring_annually: bool = False,
        is_active: bool = True,
        description: str | None = None,
    ) -> Holiday:
        """Create a new holiday.

        Args:
            name: Holiday name; combined with ``holiday_date`` must be
                unique within this company.
            holiday_date: The calendar date this holiday falls on (only
                month/day matter if ``is_recurring_annually`` is
                ``True``).
            is_recurring_annually: Whether this holiday repeats every
                year on the same month/day.
            is_active: Whether it counts toward attendance calculations
                immediately.
            description: Optional free-form notes.

        Returns:
            The newly created, persisted holiday.

        Raises:
            HolidayValidationError: If ``name`` fails length validation,
                or if this exact name/date combination is already
                defined for this company.
        """
        if not is_within_length(name, minimum=2, maximum=150):
            raise HolidayValidationError("Holiday name must be 2-150 characters.")
        self._check_duplicate(name=name, holiday_date=holiday_date)

        holiday = Holiday(
            company_id=self.company_id,
            name=name,
            holiday_date=holiday_date,
            is_recurring_annually=is_recurring_annually,
            is_active=is_active,
            description=description,
            created_by_id=self.actor_user_id,
        )
        self.holiday_repo.add(holiday)

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.CREATE,
                entity_type="Holiday",
                entity_id=holiday.id,
                description=f"Created holiday {name!r} on {holiday_date.isoformat()}.",
            )
        )
        return holiday

    def update_holiday(self, holiday: Holiday, **fields: object) -> Holiday:
        """Update a holiday's editable fields.

        Args:
            holiday: The holiday to update (must belong to this
                service's company).
            **fields: Any subset of ``name``/``holiday_date``/
                ``is_recurring_annually``/``is_active``/``description``;
                unrecognized keys are ignored.

        Returns:
            The updated holiday.

        Raises:
            HolidayValidationError: If a provided ``name`` fails length
                validation, or if the resulting name/date combination
                collides with a different holiday in this company.
        """
        if holiday.company_id != self.company_id:
            raise HolidayValidationError("This holiday does not belong to the current company.")
        if "name" in fields and not is_within_length(
            str(fields["name"]), minimum=2, maximum=150
        ):
            raise HolidayValidationError("Holiday name must be 2-150 characters.")

        new_name = str(fields.get("name", holiday.name))
        new_date = fields.get("holiday_date", holiday.holiday_date)
        if "name" in fields or "holiday_date" in fields:
            self._check_duplicate(name=new_name, holiday_date=new_date, exclude_id=holiday.id)

        holiday.update_from_dict(fields, allowed_fields=_UPDATABLE_FIELDS)
        holiday.updated_by_id = self.actor_user_id
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="Holiday",
                entity_id=holiday.id,
                description=f"Updated holiday {holiday.name!r}.",
                changes={key: str(value) for key, value in fields.items()},
            )
        )
        return holiday

    def delete_holiday(self, holiday: Holiday) -> None:
        """Soft-delete a holiday.

        Args:
            holiday: The holiday to remove from active views.
        """
        self.holiday_repo.delete(holiday)
        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.DELETE,
                entity_type="Holiday",
                entity_id=holiday.id,
                description=f"Deleted holiday {holiday.name!r}.",
            )
        )

    def list_all(self) -> list[Holiday]:
        """List every holiday defined for this company.

        Returns:
            Holidays ordered by id.
        """
        return self.holiday_repo.list_all()

    def list_active(self) -> list[Holiday]:
        """List every active holiday, ordered by date.

        Returns:
            Active holidays.
        """
        return self.holiday_repo.list_active()

    def _check_duplicate(
        self, *, name: str, holiday_date: date, exclude_id: int | None = None
    ) -> None:
        """Raise if another (non-excluded) holiday shares this name/date pair."""
        for existing in self.holiday_repo.list_all():
            if existing.id == exclude_id:
                continue
            if existing.name == name and existing.holiday_date == holiday_date:
                raise HolidayValidationError(
                    f"A holiday named {name!r} on {holiday_date.isoformat()} "
                    "already exists."
                )

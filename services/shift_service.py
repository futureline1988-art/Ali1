"""Shift management service: shift definitions and employee assignments.

Owns two related but distinct operations: CRUD on reusable
:class:`~models.shift.Shift` definitions, and assigning employees to a
shift over time via :class:`~models.shift.EmployeeShiftAssignment`. The
service is the enforcement point for the assignment invariant the model
layer explicitly defers to it (see that class's docstring): at most one
open-ended (``effective_to IS NULL``) assignment per employee, achieved
by always closing the previous one before opening a new one.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from models.audit_log import AuditLog
from models.enums import AuditAction, Weekday
from models.shift import EmployeeShiftAssignment, Shift
from repositories.audit_log_repository import AuditLogRepository
from repositories.employee_repository import EmployeeRepository
from repositories.shift_repository import EmployeeShiftAssignmentRepository, ShiftRepository
from utils.validators import is_within_length

_UPDATABLE_SHIFT_FIELDS = frozenset(
    {
        "name",
        "start_time",
        "end_time",
        "grace_period_minutes",
        "early_leave_grace_minutes",
        "overtime_threshold_minutes",
        "working_days",
        "is_active",
        "description",
    }
)


class ShiftValidationError(Exception):
    """Raised when shift or shift-assignment input fails validation."""


class ShiftService:
    """Shift operations scoped to one company.

    Attributes:
        session: The active database session.
        company_id: The company this service operates within.
        actor_user_id: The user performing these operations, recorded
            on every audit log entry.
    """

    def __init__(
        self, session: Session, *, company_id: int, actor_user_id: int | None = None
    ) -> None:
        """Create a shift service bound to one session and company.

        Args:
            session: The active database session.
            company_id: The company to operate within.
            actor_user_id: The acting user's id, for audit attribution.
        """
        self.session = session
        self.company_id = company_id
        self.actor_user_id = actor_user_id
        self.shift_repo = ShiftRepository(session, company_id=company_id)
        self.assignment_repo = EmployeeShiftAssignmentRepository(session, company_id=company_id)
        self.employee_repo = EmployeeRepository(session, company_id=company_id)
        self.audit_repo = AuditLogRepository(session)

    # ------------------------------------------------------------------
    # Shift CRUD
    # ------------------------------------------------------------------

    def create_shift(
        self,
        *,
        name: str,
        start_time,
        end_time,
        grace_period_minutes: int = 0,
        early_leave_grace_minutes: int = 0,
        overtime_threshold_minutes: int = 0,
        working_days: list[str] | None = None,
        is_active: bool = True,
        description: str | None = None,
    ) -> Shift:
        """Create a new shift definition.

        Args:
            name: Shift name; must be unique within this company.
            start_time: Clock time the shift begins.
            end_time: Clock time the shift ends (may be earlier than
                ``start_time`` for a midnight-spanning shift).
            grace_period_minutes: See :attr:`~models.shift.Shift.grace_period_minutes`.
            early_leave_grace_minutes: See
                :attr:`~models.shift.Shift.early_leave_grace_minutes`.
            overtime_threshold_minutes: See
                :attr:`~models.shift.Shift.overtime_threshold_minutes`.
            working_days: :class:`~models.enums.Weekday` values (or
                their string codes) this shift is active on; defaults to
                every day if omitted.
            is_active: Whether the shift is assignable immediately.
            description: Optional free-form notes.

        Returns:
            The newly created, persisted shift.

        Raises:
            ShiftValidationError: If ``name`` fails length validation or
                is already in use, if any minute value is negative, or
                if ``working_days`` contains an unrecognized value.
        """
        if not is_within_length(name, minimum=2, maximum=150):
            raise ShiftValidationError("Shift name must be 2-150 characters.")
        if self.shift_repo.get_by_name(name) is not None:
            raise ShiftValidationError(f"Shift name {name!r} is already in use.")
        self._validate_minute_fields(
            grace_period_minutes, early_leave_grace_minutes, overtime_threshold_minutes
        )
        resolved_days = self._resolve_working_days(working_days)

        shift = Shift(
            company_id=self.company_id,
            name=name,
            start_time=start_time,
            end_time=end_time,
            grace_period_minutes=grace_period_minutes,
            early_leave_grace_minutes=early_leave_grace_minutes,
            overtime_threshold_minutes=overtime_threshold_minutes,
            working_days=resolved_days,
            is_active=is_active,
            description=description,
            created_by_id=self.actor_user_id,
        )
        self.shift_repo.add(shift)

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.CREATE,
                entity_type="Shift",
                entity_id=shift.id,
                description=f"Created shift {name!r}.",
            )
        )
        return shift

    def update_shift(self, shift: Shift, **fields: object) -> Shift:
        """Update a shift's editable fields.

        Args:
            shift: The shift to update (must belong to this service's
                company).
            **fields: Any subset of the shift's editable fields (see
                :data:`_UPDATABLE_SHIFT_FIELDS`); unrecognized keys are
                ignored.

        Returns:
            The updated shift.

        Raises:
            ShiftValidationError: If a provided ``name`` collides with a
                different shift, a minute value is negative, or
                ``working_days`` contains an unrecognized value.
        """
        if shift.company_id != self.company_id:
            raise ShiftValidationError("This shift does not belong to the current company.")
        if "name" in fields:
            if not is_within_length(str(fields["name"]), minimum=2, maximum=150):
                raise ShiftValidationError("Shift name must be 2-150 characters.")
            existing = self.shift_repo.get_by_name(str(fields["name"]))
            if existing is not None and existing.id != shift.id:
                raise ShiftValidationError(f"Shift name {fields['name']!r} is already in use.")
        self._validate_minute_fields(
            fields.get("grace_period_minutes", 0),
            fields.get("early_leave_grace_minutes", 0),
            fields.get("overtime_threshold_minutes", 0),
            skip_defaults=True,
        )
        if "working_days" in fields:
            fields = dict(fields)
            fields["working_days"] = self._resolve_working_days(fields["working_days"])

        shift.update_from_dict(fields, allowed_fields=_UPDATABLE_SHIFT_FIELDS)
        shift.updated_by_id = self.actor_user_id
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="Shift",
                entity_id=shift.id,
                description=f"Updated shift {shift.name!r}.",
                changes={key: str(value) for key, value in fields.items()},
            )
        )
        return shift

    def delete_shift(self, shift: Shift) -> None:
        """Soft-delete a shift.

        Existing :class:`~models.shift.EmployeeShiftAssignment` rows
        referencing this shift are left untouched (they remain valid
        history for past attendance calculations); the shift simply
        stops being selectable for new assignments.

        Args:
            shift: The shift to remove from active views.
        """
        self.shift_repo.delete(shift)
        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.DELETE,
                entity_type="Shift",
                entity_id=shift.id,
                description=f"Deleted shift {shift.name!r}.",
            )
        )

    def list_all(self) -> list[Shift]:
        """List every shift defined for this company.

        Returns:
            Shifts ordered by id.
        """
        return self.shift_repo.list_all()

    def list_active(self) -> list[Shift]:
        """List every shift currently assignable in this company.

        Returns:
            Active shifts, ordered by name.
        """
        return self.shift_repo.list_active()

    # ------------------------------------------------------------------
    # Employee assignment
    # ------------------------------------------------------------------

    def assign_employee(
        self,
        *,
        employee_id: int,
        shift_id: int,
        effective_from: date,
        notes: str | None = None,
    ) -> EmployeeShiftAssignment:
        """Assign an employee to a shift starting on a given date.

        Closes the employee's previous open-ended assignment (if any)
        the day before ``effective_from``, then opens a new one — so at
        most one assignment per employee is ever open-ended, and past
        attendance calculations keep resolving to whichever shift was
        actually in effect on any given historical date.

        Args:
            employee_id: The employee to assign; must belong to this
                company.
            shift_id: The shift to assign; must belong to this company.
            effective_from: First date the new assignment applies.
            notes: Optional free-form notes (e.g. reason for the
                change).

        Returns:
            The newly created, open-ended assignment.

        Raises:
            ShiftValidationError: If ``employee_id``/``shift_id`` do not
                resolve within this company, or if ``effective_from``
                would fall on or before the start of the employee's
                current open assignment (which would create a
                zero/negative-length prior period).
        """
        employee = self.employee_repo.get_by_id(employee_id)
        if employee is None:
            raise ShiftValidationError(f"Employee {employee_id!r} was not found.")
        shift = self.shift_repo.get_by_id(shift_id)
        if shift is None:
            raise ShiftValidationError(f"Shift {shift_id!r} was not found.")

        current = self.assignment_repo.get_current_for_employee(employee_id)
        if current is not None:
            if effective_from <= current.effective_from:
                raise ShiftValidationError(
                    "The new assignment must start after the employee's current "
                    "assignment began."
                )
            current.effective_to = effective_from - timedelta(days=1)

        assignment = EmployeeShiftAssignment(
            company_id=self.company_id,
            employee_id=employee_id,
            shift_id=shift_id,
            effective_from=effective_from,
            effective_to=None,
            notes=notes,
            created_by_id=self.actor_user_id,
        )
        self.assignment_repo.add(assignment)

        self.audit_repo.add(
            AuditLog(
                company_id=self.company_id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="EmployeeShiftAssignment",
                entity_id=assignment.id,
                description=(
                    f"Assigned employee {employee.full_name!r} to shift {shift.name!r} "
                    f"effective {effective_from.isoformat()}."
                ),
            )
        )
        return assignment

    def get_current_assignment(self, employee_id: int) -> EmployeeShiftAssignment | None:
        """Fetch an employee's current, open-ended shift assignment.

        Args:
            employee_id: The employee's id.

        Returns:
            The current assignment, or ``None`` if unassigned.
        """
        return self.assignment_repo.get_current_for_employee(employee_id)

    def list_assignment_history(self, employee_id: int) -> list[EmployeeShiftAssignment]:
        """List an employee's full shift-assignment history.

        Args:
            employee_id: The employee's id.

        Returns:
            Assignments ordered from most to least recent.
        """
        return self.assignment_repo.list_for_employee(employee_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_minute_fields(
        self, *values: object, skip_defaults: bool = False
    ) -> None:
        """Reject negative minute values among grace/overtime fields."""
        for value in values:
            if value is None:
                continue
            if skip_defaults and value == 0:
                continue
            if int(value) < 0:  # type: ignore[call-overload]
                raise ShiftValidationError("Minute values cannot be negative.")

    def _resolve_working_days(self, working_days: list[str] | None) -> list[str]:
        """Normalize/validate a working-days list into stored string codes.

        Args:
            working_days: :class:`~models.enums.Weekday` values or their
                string codes; ``None``/empty means every day.

        Returns:
            The validated list of :class:`~models.enums.Weekday` string
            codes.

        Raises:
            ShiftValidationError: If any entry does not resolve to a
                valid :class:`~models.enums.Weekday`.
        """
        if not working_days:
            return [day.value for day in Weekday]
        resolved: list[str] = []
        for day in working_days:
            try:
                resolved.append(Weekday(day).value)
            except ValueError as exc:
                raise ShiftValidationError(f"{day!r} is not a valid weekday.") from exc
        return resolved

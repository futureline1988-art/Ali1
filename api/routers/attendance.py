"""Attendance endpoints — the HTTP mirror of ``controllers/attendance_controller.py``."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.dependencies import CurrentUser, get_db_session, require_permission
from api.schemas import ComputeAttendanceRequest, ManualPunchRequest
from models.attendance import AttendanceRecord
from models.enums import PunchType
from repositories.attendance_repository import AttendanceRecordRepository
from services.attendance_service import AttendanceService

router = APIRouter(prefix="/api/attendance", tags=["attendance"])


def _record_to_dict(record: AttendanceRecord) -> dict[str, Any]:
    """Serialize an attendance record plus its employee's name, while the session is open."""
    data = record.to_dict()
    data["employee_number"] = record.employee.employee_number
    data["employee_full_name"] = record.employee.full_name
    data["status_label_ar"] = record.status_label_ar
    data["status_label_en"] = record.status_label_en
    data["worked_hours"] = record.worked_hours
    data["overtime_hours"] = record.overtime_hours
    return data


@router.get("")
def list_attendance(
    start_date: date,
    end_date: date,
    employee_id: int | None = None,
    current_user: CurrentUser = Depends(
        require_permission("attendance.view", "attendance.manage")
    ),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """List attendance records within a date range, optionally for one employee."""
    repo = AttendanceRecordRepository(session, company_id=current_user.company_id)
    if employee_id is not None:
        records = repo.list_for_employee_between(employee_id, start_date, end_date)
    else:
        records = repo.list_for_company_between(start_date, end_date)
    return [_record_to_dict(record) for record in records]


@router.post("/punches", status_code=status.HTTP_201_CREATED)
def record_manual_punch(
    payload: ManualPunchRequest,
    current_user: CurrentUser = Depends(require_permission("attendance.manage")),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    """Record a manually-entered check-in/check-out punch."""
    try:
        punch_type = PunchType(payload.punch_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid punch_type: {payload.punch_type!r}.",
        ) from exc

    service = AttendanceService(session, company_id=current_user.company_id)
    service.record_manual_punch(
        employee_id=payload.employee_id,
        punch_type=punch_type,
        punch_time=payload.punch_time,
        notes=payload.notes,
    )
    return {"status": "recorded"}


@router.post("/compute")
def compute_daily_attendance(
    payload: ComputeAttendanceRequest,
    current_user: CurrentUser = Depends(require_permission("attendance.manage")),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Compute (or recompute) one employee's attendance for one day."""
    service = AttendanceService(session, company_id=current_user.company_id)
    record = service.compute_daily_attendance(
        employee_id=payload.employee_id, work_date=payload.work_date
    )
    return _record_to_dict(record)

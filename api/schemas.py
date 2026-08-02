"""Pydantic request/response models for the REST API.

Response bodies for read endpoints are deliberately plain ``dict``
(built from :meth:`~models.base.SerializationMixin.to_dict`, already
JSON-safe) rather than a strict Pydantic model — the same ORM-to-dict
path the desktop controllers use. Pydantic models here are for
*request* validation, where rejecting a malformed payload before it
reaches the service layer is the entire point.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """POST /api/auth/login request body."""

    company_id: int
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class EmployeeCreateRequest(BaseModel):
    """POST /api/employees request body."""

    employee_number: str = Field(min_length=1)
    full_name: str = Field(min_length=1)
    department_id: int | None = None
    branch_id: int | None = None
    national_id: str | None = None
    email: str | None = None
    phone: str | None = None
    position: str | None = None
    salary: Decimal | None = None
    hire_date: date | None = None
    notes: str | None = None


class EmployeeUpdateRequest(BaseModel):
    """PUT /api/employees/{id} request body — every field optional (partial update)."""

    full_name: str | None = None
    department_id: int | None = None
    branch_id: int | None = None
    national_id: str | None = None
    email: str | None = None
    phone: str | None = None
    position: str | None = None
    salary: Decimal | None = None
    hire_date: date | None = None
    notes: str | None = None


class DepartmentCreateRequest(BaseModel):
    """POST /api/departments request body."""

    name: str = Field(min_length=1)
    code: str | None = None
    description: str | None = None
    parent_department_id: int | None = None


class ManualPunchRequest(BaseModel):
    """POST /api/attendance/punches request body."""

    employee_id: int
    punch_type: str
    punch_time: datetime | None = None
    notes: str | None = None


class ComputeAttendanceRequest(BaseModel):
    """POST /api/attendance/compute request body."""

    employee_id: int
    work_date: date

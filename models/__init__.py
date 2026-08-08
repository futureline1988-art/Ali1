"""SQLAlchemy ORM domain models for the Attendance Management System.

Every model submodule is imported here so that importing anything from
this package — even indirectly, e.g. ``database.database``'s
``from models.base import Base`` — always registers every table on
:data:`~models.base.Base`'s metadata. Without this, ``Base.metadata``
would only contain whichever models some other, unrelated import chain
happened to have already pulled in, and
:meth:`~database.database.Database.create_all_tables` would silently
create an incomplete schema (or none at all) depending on import order.
"""

from __future__ import annotations

from models.attendance import AttendancePunch, AttendanceRecord
from models.audit_log import AuditLog
from models.branch import Branch
from models.company import Company
from models.company_settings import CompanySettings
from models.department import Department
from models.device import Device
from models.employee import Employee
from models.holiday import Holiday
from models.leave import LeavePolicy, LeaveRequest
from models.payroll import (
    PayrollAdjustment,
    PayrollAutomaticRule,
    PayrollRun,
    PayrollRunLine,
    PayrollRunSnapshot,
)
from models.permission import Permission
from models.role import Role
from models.shift import EmployeeShiftAssignment, Shift
from models.update_credential import UpdateServerCredential
from models.update_state import ClientUpdateState, ClientUpdateStatus
from models.user import User

__all__ = [
    "AttendancePunch",
    "AttendanceRecord",
    "AuditLog",
    "Branch",
    "ClientUpdateState",
    "ClientUpdateStatus",
    "Company",
    "CompanySettings",
    "Department",
    "Device",
    "Employee",
    "EmployeeShiftAssignment",
    "Holiday",
    "LeavePolicy",
    "LeaveRequest",
    "PayrollAdjustment",
    "PayrollAutomaticRule",
    "PayrollRun",
    "PayrollRunLine",
    "PayrollRunSnapshot",
    "Permission",
    "Role",
    "Shift",
    "UpdateServerCredential",
    "User",
]

"""Developer Suite ORM models: platform administration data only.

Per the platform's ownership boundary (see the top-level
``developer_suite/__init__.py``), nothing here ever represents customer
*operational* data (employees, attendance, departments, shifts,
reports) — that always lives in the customer's own Attendance Client
database. Models here represent the vendor's own records *about* its
customers, licenses, and (as of Phase 5) reusable remote-configuration
templates.
"""

from __future__ import annotations

from developer_suite.models.admin_token import AdminBootstrapToken
from developer_suite.models.attendance_policy_profile import AttendancePolicyProfile
from developer_suite.models.backup_profile import BackupLocationType, BackupProfile
from developer_suite.models.customer import Customer, CustomerStatus
from developer_suite.models.device_profile import DeviceProfile
from developer_suite.models.license import IssuedLicense, IssuedLicenseStatus
from developer_suite.models.print_profile import PaperSize, PrintProfile
from developer_suite.models.remote_configuration import RemoteConfiguration
from developer_suite.models.sync_state import (
    OutboxStatus,
    SyncCursor,
    SyncDeviceCredential,
    SyncEntityVersion,
    SyncOperation,
    SyncOutboxEntry,
)
from developer_suite.models.theme_profile import ThemeMode, ThemeProfile

__all__ = [
    "Customer",
    "CustomerStatus",
    "IssuedLicense",
    "IssuedLicenseStatus",
    "ThemeProfile",
    "ThemeMode",
    "PrintProfile",
    "PaperSize",
    "AttendancePolicyProfile",
    "DeviceProfile",
    "BackupProfile",
    "BackupLocationType",
    "RemoteConfiguration",
    "SyncDeviceCredential",
    "SyncCursor",
    "SyncEntityVersion",
    "SyncOutboxEntry",
    "SyncOperation",
    "OutboxStatus",
    "AdminBootstrapToken",
]

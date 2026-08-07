"""Shared, bilingual enumerations used across models, services and the UI.

Every enum in this module stores a stable, English, database-safe string
as its value (so schema data never depends on the active UI language),
while also carrying an Arabic and an English display label accessible
through :attr:`BilingualEnum.label_ar` / :attr:`BilingualEnum.label_en`.
This lets combo boxes, tables and generated reports render the correct
language without a separate translation lookup table.

Example:
    >>> UserRole.HR.value
    'hr'
    >>> UserRole.HR.label_ar
    'الموارد البشرية'
    >>> UserRole.HR.label(language="en")
    'Human Resources'
"""

from __future__ import annotations

from enum import Enum


class BilingualEnum(str, Enum):
    """Base class for string enums that carry Arabic/English labels.

    Subclasses declare members as 3-tuples of
    ``(value, label_ar, label_en)``. Because this class also inherits
    from :class:`str`, members compare and serialize exactly like plain
    strings (``UserRole.HR == "hr"``), which makes them safe to store
    directly in SQLAlchemy ``Enum`` columns and to dump to JSON.
    """

    def __new__(cls, value: str, label_ar: str, label_en: str) -> "BilingualEnum":
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj._label_ar = label_ar
        obj._label_en = label_en
        return obj

    @property
    def label_ar(self) -> str:
        """The Arabic display label for this member."""
        return self._label_ar

    @property
    def label_en(self) -> str:
        """The English display label for this member."""
        return self._label_en

    def label(self, language: str = "ar") -> str:
        """Return the display label for the requested language.

        Args:
            language: Either ``"ar"`` or ``"en"``. Any other value falls
                back to the Arabic label, since Arabic is this
                application's default language.

        Returns:
            The Arabic or English label for this member.
        """
        return self._label_en if language == "en" else self._label_ar


class UserRole(BilingualEnum):
    """System user roles, from broadest to most restricted privileges."""

    SYSTEM_ADMIN = ("system_admin", "مدير النظام", "System Administrator")
    MANAGER = ("manager", "المدير", "Manager")
    HR = ("hr", "الموارد البشرية", "Human Resources")
    SUPERVISOR = ("supervisor", "المشرف", "Supervisor")
    USER = ("user", "مستخدم عادي", "Regular User")


class EmploymentStatus(BilingualEnum):
    """Current employment state of an :class:`~models.employee.Employee`."""

    ACTIVE = ("active", "نشط", "Active")
    ON_LEAVE = ("on_leave", "في إجازة", "On Leave")
    SUSPENDED = ("suspended", "موقوف", "Suspended")
    RESIGNED = ("resigned", "مستقيل", "Resigned")
    TERMINATED = ("terminated", "منتهي الخدمة", "Terminated")


class Weekday(BilingualEnum):
    """Days of the week, ordered starting Saturday (the Iraqi work week)."""

    SATURDAY = ("saturday", "السبت", "Saturday")
    SUNDAY = ("sunday", "الأحد", "Sunday")
    MONDAY = ("monday", "الاثنين", "Monday")
    TUESDAY = ("tuesday", "الثلاثاء", "Tuesday")
    WEDNESDAY = ("wednesday", "الأربعاء", "Wednesday")
    THURSDAY = ("thursday", "الخميس", "Thursday")
    FRIDAY = ("friday", "الجمعة", "Friday")


class PunchType(BilingualEnum):
    """Direction of a single raw attendance punch (manual or device)."""

    CHECK_IN = ("check_in", "حضور", "Check In")
    CHECK_OUT = ("check_out", "انصراف", "Check Out")


class AttendanceSource(BilingualEnum):
    """Where an attendance punch originated from."""

    MANUAL = ("manual", "تسجيل يدوي", "Manual Entry")
    DEVICE = ("device", "جهاز بصمة", "Biometric Device")
    API = ("api", "استيراد API", "API Import")


class AttendanceDayStatus(BilingualEnum):
    """Computed status of an employee's attendance for a single day."""

    PRESENT = ("present", "حاضر", "Present")
    LATE = ("late", "متأخر", "Late")
    EARLY_LEAVE = ("early_leave", "انصراف مبكر", "Early Leave")
    ABSENT = ("absent", "غائب", "Absent")
    ON_LEAVE = ("on_leave", "إجازة", "On Leave")
    HOLIDAY = ("holiday", "عطلة رسمية", "Official Holiday")
    WEEKEND = ("weekend", "عطلة أسبوعية", "Weekend")


class LeaveType(BilingualEnum):
    """Category of a leave request."""

    ANNUAL = ("annual", "إجازة سنوية", "Annual Leave")
    SICK = ("sick", "إجازة مرضية", "Sick Leave")
    EMERGENCY = ("emergency", "إجازة طارئة", "Emergency Leave")
    UNPAID = ("unpaid", "إجازة بدون راتب", "Unpaid Leave")
    OTHER = ("other", "إجازة أخرى", "Other")


class LeaveStatus(BilingualEnum):
    """Approval workflow state of a leave request."""

    PENDING = ("pending", "قيد الانتظار", "Pending")
    APPROVED = ("approved", "موافق عليها", "Approved")
    REJECTED = ("rejected", "مرفوضة", "Rejected")
    CANCELLED = ("cancelled", "ملغاة", "Cancelled")


class DeviceProtocol(BilingualEnum):
    """Supported biometric device communication protocols."""

    ZKTECO_TCP = ("zkteco_tcp", "ZKTeco TCP/IP", "ZKTeco TCP/IP")
    ZKTECO_UDP = ("zkteco_udp", "ZKTeco UDP", "ZKTeco UDP")
    HIKVISION = ("hikvision", "Hikvision", "Hikvision")
    DELI_ES172 = ("deli_es172", "DELI ES172", "DELI ES172")


class DeviceStatus(BilingualEnum):
    """Last-known connectivity state of a registered device."""

    ONLINE = ("online", "متصل", "Online")
    OFFLINE = ("offline", "غير متصل", "Offline")
    ERROR = ("error", "خطأ", "Error")
    UNKNOWN = ("unknown", "غير معروف", "Unknown")


class DeviceOperationType(BilingualEnum):
    """A discrete operation that can be run against a biometric device."""

    TEST_CONNECTION = ("test_connection", "اختبار الاتصال", "Test Connection")
    DISCOVERY = ("discovery", "اكتشاف الأجهزة", "Device Discovery")
    DOWNLOAD_USERS = ("download_users", "جلب المستخدمين", "Download Users")
    UPLOAD_USERS = ("upload_users", "رفع المستخدمين", "Upload Users")
    DOWNLOAD_ATTENDANCE_LOGS = (
        "download_attendance_logs",
        "سحب سجلات الحضور",
        "Download Attendance Logs",
    )
    DOWNLOAD_FINGERPRINTS = (
        "download_fingerprints",
        "جلب بصمات الأصابع",
        "Download Fingerprints",
    )
    UPLOAD_FINGERPRINTS = (
        "upload_fingerprints",
        "رفع بصمات الأصابع",
        "Upload Fingerprints",
    )


class ReportFormat(BilingualEnum):
    """File format a report can be exported to."""

    EXCEL = ("excel", "إكسل", "Excel")
    PDF = ("pdf", "PDF", "PDF")
    CSV = ("csv", "CSV", "CSV")


class ReportPeriod(BilingualEnum):
    """Time granularity of a generated attendance report."""

    DAILY = ("daily", "يومي", "Daily")
    WEEKLY = ("weekly", "أسبوعي", "Weekly")
    MONTHLY = ("monthly", "شهري", "Monthly")
    ANNUAL = ("annual", "سنوي", "Annual")
    CUSTOM_RANGE = ("custom_range", "فترة مخصصة", "Custom Range")


class ReportType(BilingualEnum):
    """Subject/dimension a report is built around."""

    ATTENDANCE_SUMMARY = ("attendance_summary", "ملخص الحضور", "Attendance Summary")
    BY_EMPLOYEE = ("by_employee", "حسب الموظف", "By Employee")
    BY_DEPARTMENT = ("by_department", "حسب القسم", "By Department")
    BY_DEVICE = ("by_device", "حسب الجهاز", "By Device")
    LATE_EMPLOYEES = ("late_employees", "الموظفون المتأخرون", "Late Employees")
    OVERTIME = ("overtime", "الوقت الإضافي", "Overtime")
    ABSENCE = ("absence", "الغياب", "Absence")


class AuditAction(BilingualEnum):
    """Kind of action recorded in the audit/activity log."""

    LOGIN = ("login", "تسجيل دخول", "Login")
    LOGOUT = ("logout", "تسجيل خروج", "Logout")
    LOGIN_FAILED = ("login_failed", "فشل تسجيل الدخول", "Login Failed")
    CREATE = ("create", "إنشاء", "Create")
    UPDATE = ("update", "تعديل", "Update")
    DELETE = ("delete", "حذف", "Delete")
    RESTORE = ("restore", "استعادة", "Restore")
    EXPORT = ("export", "تصدير", "Export")
    BACKUP = ("backup", "نسخ احتياطي", "Backup")
    RESTORE_BACKUP = ("restore_backup", "استعادة نسخة احتياطية", "Restore Backup")
    DEVICE_SYNC = ("device_sync", "مزامنة جهاز", "Device Sync")

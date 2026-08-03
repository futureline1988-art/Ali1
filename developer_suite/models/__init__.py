"""Developer Suite ORM models: platform administration data only.

Per the platform's ownership boundary (see the top-level
``developer_suite/__init__.py``), nothing here ever represents customer
*operational* data (employees, attendance, departments, shifts,
reports) — that always lives in the customer's own Attendance Client
database. Models here represent the vendor's own records *about* its
customers and licenses.
"""

from __future__ import annotations

from developer_suite.models.customer import Customer, CustomerStatus
from developer_suite.models.license import IssuedLicense, IssuedLicenseStatus

__all__ = ["Customer", "CustomerStatus", "IssuedLicense", "IssuedLicenseStatus"]

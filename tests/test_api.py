"""REST API: the same RBAC/business-logic guarantees, verified over real HTTP.

Uses FastAPI's ``TestClient`` against the exact isolated database
``db_session`` builds for each test — the API layer under test talks
to that database through the same ``get_db_session``/``session_scope``
path it uses in production, no mocking.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from database.database import session_scope
from repositories.role_repository import RoleRepository
from services.company_service import CompanyService
from services.department_service import DepartmentService
from services.employee_service import EmployeeService
from services.user_service import UserService


@pytest.fixture
def api_context(seeded_permissions):
    """Build a company with an admin user, a dashboard-only user, a department, and an employee.

    Returns:
        A dict with ``client``, ``company_id``, ``admin_headers``,
        ``limited_headers``, ``department_id``, and ``employee_id``.
    """
    with session_scope() as session:
        company = CompanyService(session).create_company(name="شركة اختبار REST")
        company_id = company.id

        roles = RoleRepository(session, company_id=company_id).list_all()
        admin_role = next(r for r in roles if r.code == "system_admin")
        limited_role = next(r for r in roles if r.code == "user")

        UserService(session, company_id=company_id).create_user(
            username="admin", full_name="مدير", password="AdminPass123!", role_id=admin_role.id
        )
        UserService(session, company_id=company_id).create_user(
            username="limited", full_name="محدود", password="LimitedPass123!",
            role_id=limited_role.id,
        )

        department = DepartmentService(session, company_id=company_id).create_department(
            name="قسم الاختبار"
        )
        department_id = department.id

        employee = EmployeeService(session, company_id=company_id).create_employee(
            employee_number="SEED-001", full_name="موظف بذرة", department_id=department_id
        )
        employee_id = employee.id

    from api.app import create_app

    client = TestClient(create_app())

    def _login(username: str, password: str) -> str:
        response = client.post(
            "/api/auth/login",
            json={"company_id": company_id, "username": username, "password": password},
        )
        assert response.status_code == 200, response.text
        return response.json()["access_token"]

    admin_token = _login("admin", "AdminPass123!")
    limited_token = _login("limited", "LimitedPass123!")

    return {
        "client": client,
        "company_id": company_id,
        "admin_headers": {"Authorization": f"Bearer {admin_token}"},
        "limited_headers": {"Authorization": f"Bearer {limited_token}"},
        "department_id": department_id,
        "employee_id": employee_id,
    }


def test_health_check_is_unauthenticated(api_context):
    response = api_context["client"].get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_companies_listing_is_public(api_context):
    response = api_context["client"].get("/api/companies")
    assert response.status_code == 200
    names = [company["name"] for company in response.json()]
    assert "شركة اختبار REST" in names


def test_login_with_wrong_password_returns_401(api_context):
    response = api_context["client"].post(
        "/api/auth/login",
        json={"company_id": api_context["company_id"], "username": "admin", "password": "wrong"},
    )
    assert response.status_code == 401


def test_login_response_never_leaks_password_hash(api_context):
    response = api_context["client"].post(
        "/api/auth/login",
        json={
            "company_id": api_context["company_id"],
            "username": "admin",
            "password": "AdminPass123!",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert "password_hash" not in body["user"]
    assert "employees.manage" in body["user"]["permission_codes"]


def test_protected_route_without_token_returns_401(api_context):
    response = api_context["client"].get("/api/employees")
    assert response.status_code == 401


def test_tampered_token_returns_401(api_context):
    bad_headers = {"Authorization": api_context["admin_headers"]["Authorization"] + "tampered"}
    response = api_context["client"].get("/api/employees", headers=bad_headers)
    assert response.status_code == 401


def test_rbac_denies_dashboard_only_user_on_employees(api_context):
    response = api_context["client"].get("/api/employees", headers=api_context["limited_headers"])
    assert response.status_code == 403


def test_rbac_allows_dashboard_only_user_on_dashboard(api_context):
    response = api_context["client"].get(
        "/api/dashboard/summary", headers=api_context["limited_headers"]
    )
    assert response.status_code == 200


def test_list_employees_as_admin(api_context):
    response = api_context["client"].get("/api/employees", headers=api_context["admin_headers"])
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["employee_number"] == "SEED-001"


def test_create_employee_as_admin(api_context):
    response = api_context["client"].post(
        "/api/employees",
        headers=api_context["admin_headers"],
        json={
            "employee_number": "API-001",
            "full_name": "موظف عبر API",
            "department_id": api_context["department_id"],
        },
    )
    assert response.status_code == 201
    assert response.json()["department_name"] == "قسم الاختبار"


def test_create_employee_duplicate_number_returns_422(api_context):
    client, headers = api_context["client"], api_context["admin_headers"]
    payload = {"employee_number": "DUP-1", "full_name": "الأول"}
    assert client.post("/api/employees", headers=headers, json=payload).status_code == 201
    payload["full_name"] = "الثاني"
    response = client.post("/api/employees", headers=headers, json=payload)
    assert response.status_code == 422


def test_update_employee_partial_fields(api_context):
    client, headers = api_context["client"], api_context["admin_headers"]
    response = client.put(
        f"/api/employees/{api_context['employee_id']}", headers=headers, json={"position": "مطور"}
    )
    assert response.status_code == 200
    assert response.json()["position"] == "مطور"
    assert response.json()["full_name"] == "موظف بذرة"  # untouched


def test_get_unknown_employee_returns_404(api_context):
    response = api_context["client"].get("/api/employees/999999", headers=api_context["admin_headers"])
    assert response.status_code == 404


def test_delete_employee_then_list_no_longer_shows_it(api_context):
    client, headers = api_context["client"], api_context["admin_headers"]
    response = client.delete(f"/api/employees/{api_context['employee_id']}", headers=headers)
    assert response.status_code == 204
    response = client.get("/api/employees", headers=headers)
    assert response.json() == []


def test_manual_punch_and_compute_attendance(api_context):
    client, headers = api_context["client"], api_context["admin_headers"]
    employee_id = api_context["employee_id"]
    work_date = date.today()

    for punch_type, punch_time in (
        ("check_in", f"{work_date.isoformat()}T09:00:00+03:00"),
        ("check_out", f"{work_date.isoformat()}T17:00:00+03:00"),
    ):
        response = client.post(
            "/api/attendance/punches",
            headers=headers,
            json={"employee_id": employee_id, "punch_type": punch_type, "punch_time": punch_time},
        )
        assert response.status_code == 201, response.text

    response = client.post(
        "/api/attendance/compute",
        headers=headers,
        json={"employee_id": employee_id, "work_date": work_date.isoformat()},
    )
    assert response.status_code == 200
    record = response.json()
    assert record["employee_number"] == "SEED-001"
    assert record["status"] in ("present", "late", "absent", "weekend")


def test_invalid_punch_type_returns_422(api_context):
    response = api_context["client"].post(
        "/api/attendance/punches",
        headers=api_context["admin_headers"],
        json={"employee_id": api_context["employee_id"], "punch_type": "not_a_real_type"},
    )
    assert response.status_code == 422


def test_list_attendance_by_date_range(api_context):
    client, headers = api_context["client"], api_context["admin_headers"]
    work_date = date.today()
    client.post(
        "/api/attendance/compute",
        headers=headers,
        json={"employee_id": api_context["employee_id"], "work_date": work_date.isoformat()},
    )
    response = client.get(
        "/api/attendance",
        headers=headers,
        params={
            "start_date": (work_date - timedelta(days=1)).isoformat(),
            "end_date": work_date.isoformat(),
            "employee_id": api_context["employee_id"],
        },
    )
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_dashboard_endpoints_return_expected_shape(api_context):
    client, headers = api_context["client"], api_context["admin_headers"]

    summary = client.get("/api/dashboard/summary", headers=headers)
    assert summary.status_code == 200
    assert summary.json()["active_employee_count"] == 1

    trend = client.get("/api/dashboard/trend", headers=headers)
    assert trend.status_code == 200
    assert len(trend.json()) == 14

    departments = client.get("/api/dashboard/departments", headers=headers)
    assert departments.status_code == 200
    assert {row["name"] for row in departments.json()} == {"قسم الاختبار"}


def test_department_create_and_delete(api_context):
    client, headers = api_context["client"], api_context["admin_headers"]
    response = client.post("/api/departments", headers=headers, json={"name": "قسم جديد"})
    assert response.status_code == 201
    new_id = response.json()["id"]

    response = client.get("/api/departments", headers=headers)
    assert len(response.json()) == 2

    response = client.delete(f"/api/departments/{new_id}", headers=headers)
    assert response.status_code == 204

    response = client.get("/api/departments", headers=headers)
    assert len(response.json()) == 1

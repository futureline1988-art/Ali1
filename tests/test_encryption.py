"""Field-level encryption: round-trip correctness and at-rest transparency.

The encryption key lives in the real (development) data directory
(``config.PathsConfig.data_dir`` equals ``BASE_DIR`` outside a frozen
build — see ``config.py``'s ``_resolve_data_root``), not the per-test
temp database, so these tests do write and read
``data/.field_encryption.key`` in the working tree like every other
encryption test this project has run. ``tests/conftest.py`` cannot
isolate that path away without changing ``config.py``'s own documented
dev-mode behavior, so cleanup is the test runner's responsibility (see
the project's established "clean up data/ after a test round" habit).
"""

from __future__ import annotations

from decimal import Decimal

from database.database import session_scope
from models.enums import DeviceProtocol
from services.device_service import DeviceService
from services.employee_service import EmployeeService
from utils.encryption import decrypt_text, encrypt_text


def test_encrypt_decrypt_round_trip():
    plaintext = "سر الشركة - راتب سري 12345"
    ciphertext = encrypt_text(plaintext)
    assert ciphertext != plaintext
    assert decrypt_text(ciphertext) == plaintext


def test_encryption_is_randomized_not_deterministic():
    """Two encryptions of the same plaintext must not produce identical ciphertext."""
    first = encrypt_text("same value")
    second = encrypt_text("same value")
    assert first != second
    assert decrypt_text(first) == decrypt_text(second) == "same value"


def test_employee_salary_is_encrypted_at_rest(company_factory, db_session):
    """The raw SQLite column must not contain the plaintext salary."""
    company_id = company_factory()
    with session_scope() as session:
        employee = EmployeeService(session, company_id=company_id).create_employee(
            employee_number="ENC-001", full_name="موظف مشفر", salary=Decimal("1500.50")
        )
        employee_id = employee.id

    # Read the raw column value, bypassing the ORM's TypeDecorator entirely.
    raw_connection = db_session.engine.raw_connection()
    try:
        cursor = raw_connection.cursor()
        cursor.execute("SELECT salary FROM employees WHERE id = ?", (employee_id,))
        raw_value = cursor.fetchone()[0]
    finally:
        raw_connection.close()

    assert raw_value is not None
    assert "1500.50" not in raw_value
    assert "1500.5" not in raw_value

    # The ORM must still transparently decrypt it back to the original Decimal.
    with session_scope() as session:
        employee = EmployeeService(session, company_id=company_id).employee_repo.get_by_id(
            employee_id
        )
        assert employee.salary == Decimal("1500.50")


def test_device_communication_key_is_encrypted_at_rest(company_factory, db_session):
    company_id = company_factory()
    with session_scope() as session:
        device = DeviceService(session, company_id=company_id).create_device(
            name="جهاز البصمة",
            protocol=DeviceProtocol.ZKTECO_TCP,
            host="192.168.1.50",
            port=4370,
            communication_key="super-secret-comm-key",
        )
        device_id = device.id

    raw_connection = db_session.engine.raw_connection()
    try:
        cursor = raw_connection.cursor()
        cursor.execute("SELECT communication_key FROM devices WHERE id = ?", (device_id,))
        raw_value = cursor.fetchone()[0]
    finally:
        raw_connection.close()

    assert "super-secret-comm-key" not in raw_value

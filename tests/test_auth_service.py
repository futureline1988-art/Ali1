"""AuthService: login, lockout, and password change — the account-security core."""

from __future__ import annotations

import pytest

from database.database import session_scope
from repositories.role_repository import RoleRepository
from services.auth_service import AuthenticationError, AuthService
from services.user_service import UserService


@pytest.fixture
def user_fixture(company_factory):
    """Create one company with one user with a known password.

    Returns:
        A dict with ``company_id``, ``user_id``, and ``password``.
    """
    company_id = company_factory()
    password = "CorrectHorse123"
    with session_scope() as session:
        role = RoleRepository(session, company_id=company_id).list_all()[0]
        user = UserService(session, company_id=company_id).create_user(
            username="auth_user", full_name="مستخدم", password=password, role_id=role.id
        )
        user_id = user.id
    return {"company_id": company_id, "user_id": user_id, "password": password}


def test_login_with_correct_credentials_succeeds(user_fixture):
    with session_scope() as session:
        service = AuthService(session, company_id=user_fixture["company_id"])
        user = service.login("auth_user", user_fixture["password"])
    assert user.id == user_fixture["user_id"]


def test_login_with_wrong_password_raises(user_fixture):
    with session_scope() as session:
        service = AuthService(session, company_id=user_fixture["company_id"])
        with pytest.raises(AuthenticationError):
            service.login("auth_user", "wrong-password")


def test_login_with_unknown_username_raises_generic_error(user_fixture):
    """Unknown-username and wrong-password must not be distinguishable."""
    with session_scope() as session:
        service = AuthService(session, company_id=user_fixture["company_id"])
        try:
            service.login("no-such-user", "irrelevant")
            pytest.fail("expected AuthenticationError")
        except AuthenticationError as unknown_exc:
            unknown_message = str(unknown_exc)
        try:
            service.login("auth_user", "wrong-password")
            pytest.fail("expected AuthenticationError")
        except AuthenticationError as wrong_exc:
            wrong_message = str(wrong_exc)
    assert unknown_message == wrong_message


def test_repeated_failures_lock_the_account(user_fixture):
    from config import get_config

    max_attempts = get_config().security.max_login_attempts

    with session_scope() as session:
        service = AuthService(session, company_id=user_fixture["company_id"])
        for _ in range(max_attempts):
            with pytest.raises(AuthenticationError):
                service.login("auth_user", "wrong-password")

    with session_scope() as session:
        service = AuthService(session, company_id=user_fixture["company_id"])
        with pytest.raises(AuthenticationError, match="locked"):
            service.login("auth_user", user_fixture["password"])


def test_change_password_requires_correct_current_password(user_fixture):
    with session_scope() as session:
        from repositories.user_repository import UserRepository

        user = UserRepository(session, company_id=user_fixture["company_id"]).get_by_id(
            user_fixture["user_id"]
        )
        service = AuthService(session, company_id=user_fixture["company_id"])
        with pytest.raises(AuthenticationError):
            service.change_password(
                user, current_password="wrong", new_password="NewPassword123"
            )


def test_change_password_then_login_with_new_password(user_fixture):
    with session_scope() as session:
        from repositories.user_repository import UserRepository

        user = UserRepository(session, company_id=user_fixture["company_id"]).get_by_id(
            user_fixture["user_id"]
        )
        service = AuthService(session, company_id=user_fixture["company_id"])
        service.change_password(
            user, current_password=user_fixture["password"], new_password="BrandNewPass123"
        )

    with session_scope() as session:
        service = AuthService(session, company_id=user_fixture["company_id"])
        user = service.login("auth_user", "BrandNewPass123")
    assert user.id == user_fixture["user_id"]


def test_change_password_rejects_weak_new_password(user_fixture):
    with session_scope() as session:
        from repositories.user_repository import UserRepository

        user = UserRepository(session, company_id=user_fixture["company_id"]).get_by_id(
            user_fixture["user_id"]
        )
        service = AuthService(session, company_id=user_fixture["company_id"])
        with pytest.raises(ValueError):
            service.change_password(
                user, current_password=user_fixture["password"], new_password="short"
            )

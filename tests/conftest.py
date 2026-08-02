"""Shared pytest fixtures: an isolated real SQLite database per test.

Every fixture here drives the exact same code path the desktop app and
REST API use in production (:func:`~database.database.session_scope`,
the service layer, the permission catalog seeded by
``main._seed_default_permissions``) rather than mocks — matching this
project's testing philosophy established throughout its development:
real SQLite, real ORM, real service/controller code, verified end to
end.

:mod:`config` and :mod:`database.database` each hold a process-wide
lazily-initialized singleton (``get_config()``/``get_database()``).
Since pytest runs every test in one process, :func:`db_session` resets
both singletons before and after each test so one test's database
never leaks into the next.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APP_ENVIRONMENT", "testing")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import config as config_module
from database import database as database_module


def _reset_singletons() -> None:
    """Clear the process-wide config/database singletons."""
    config_module._config_instance = None
    database_module._database_instance = None


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    """Provide a fresh, isolated, fully-migrated SQLite database for one test.

    Tests open their own units of work via
    ``from database.database import session_scope`` (the same pattern
    every service/controller in this codebase uses) rather than this
    fixture handing back a session directly, so tests exercise the
    exact commit/rollback boundaries production code does.
    """
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_SQLITE_PATH", str(db_path))
    _reset_singletons()

    database = database_module.get_database()
    database.initialize()

    yield database

    database_module.get_database().dispose()
    _reset_singletons()


@pytest.fixture
def seeded_permissions(db_session):
    """Seed the global permission catalog (a prerequisite for any role/RBAC test)."""
    from main import _seed_default_permissions

    _seed_default_permissions()


@pytest.fixture
def company_factory(seeded_permissions):
    """Return a callable that onboards a new company with its default roles seeded."""
    from database.database import session_scope
    from services.company_service import CompanyService

    def _create(name: str = "شركة اختبار"):
        with session_scope() as session:
            company = CompanyService(session).create_company(name=name)
            return company.id

    return _create

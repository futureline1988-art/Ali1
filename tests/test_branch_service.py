"""BranchService: the "at most one main branch per company" invariant."""

from __future__ import annotations

import pytest

from database.database import session_scope
from services.branch_service import BranchService, BranchValidationError


def test_first_branch_can_be_the_main_branch(company_factory):
    company_id = company_factory()
    with session_scope() as session:
        branch = BranchService(session, company_id=company_id).create_branch(
            name="الفرع الرئيسي", is_main_branch=True
        )
        assert branch.is_main_branch is True


def test_designating_a_new_main_branch_unmarks_the_old_one(company_factory):
    company_id = company_factory()
    with session_scope() as session:
        service = BranchService(session, company_id=company_id)
        first = service.create_branch(name="فرع 1", is_main_branch=True)
        second = service.create_branch(name="فرع 2", is_main_branch=True)
        session.refresh(first)

    assert first.is_main_branch is False
    assert second.is_main_branch is True

    with session_scope() as session:
        main = BranchService(session, company_id=company_id).get_main_branch()
        assert main.name == "فرع 2"


def test_updating_is_main_branch_unmarks_the_previous_one(company_factory):
    company_id = company_factory()
    with session_scope() as session:
        service = BranchService(session, company_id=company_id)
        first = service.create_branch(name="فرع أ", is_main_branch=True)
        second = service.create_branch(name="فرع ب", is_main_branch=False)
        second_id = second.id

    with session_scope() as session:
        service = BranchService(session, company_id=company_id)
        second = service.branch_repo.get_by_id(second_id)
        service.update_branch(second, is_main_branch=True)

    with session_scope() as session:
        main = BranchService(session, company_id=company_id).get_main_branch()
        assert main.name == "فرع ب"


def test_duplicate_branch_name_rejected(company_factory):
    company_id = company_factory()
    with session_scope() as session:
        service = BranchService(session, company_id=company_id)
        service.create_branch(name="الفرع")
        with pytest.raises(BranchValidationError, match="already in use"):
            service.create_branch(name="الفرع")


def test_delete_branch_is_soft_delete(company_factory):
    company_id = company_factory()
    with session_scope() as session:
        service = BranchService(session, company_id=company_id)
        branch = service.create_branch(name="فرع للحذف")
        branch_id = branch.id
        service.delete_branch(branch)

    with session_scope() as session:
        service = BranchService(session, company_id=company_id)
        assert service.branch_repo.get_by_id(branch_id) is None
        assert len(service.list_all()) == 0

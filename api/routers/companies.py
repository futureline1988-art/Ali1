"""Public company listing — the REST equivalent of the login window's company picker.

Deliberately unauthenticated (a caller cannot have a bearer token
before they know which company to authenticate against — see
``ui/login_window.py``'s own combo box, populated the same way before
any login attempt), and deliberately thin: only the fields a picker
needs, not the full :class:`~models.company.Company` record.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.dependencies import get_db_session
from repositories.company_repository import CompanyRepository

router = APIRouter(prefix="/api/companies", tags=["companies"])


@router.get("")
def list_companies(session: Session = Depends(get_db_session)) -> list[dict[str, int | str]]:
    """List every active company, for the login step's company picker."""
    companies = CompanyRepository(session).list_active()
    return [{"id": company.id, "name": company.name} for company in companies]

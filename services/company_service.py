"""Company (tenant) onboarding and administration service.

This is the concrete fulfillment of "new companies can be added without
changing the code": :meth:`CompanyService.create_company` does
everything a new tenant needs to start working immediately — the
``Company`` row, its default :class:`~models.company_settings.CompanySettings`,
and its own independent copy of the five built-in
:class:`~models.enums.UserRole` kinds as real
:class:`~models.role.Role` rows (see that model's docstring on why
every company gets its own role rows instead of sharing global ones).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from models.audit_log import AuditLog
from models.company import Company
from models.enums import AuditAction, UserRole
from models.role import Role
from repositories.audit_log_repository import AuditLogRepository
from repositories.company_repository import CompanyRepository
from repositories.company_settings_repository import CompanySettingsRepository
from repositories.permission_repository import PermissionRepository
from repositories.role_repository import RoleRepository
from utils.validators import is_within_length

# Default permission-code grants for each seeded built-in role, applied once
# at company creation (an admin can freely edit them per-company afterwards
# via the Roles screen - see services/user_service.py's update_role_permissions).
# SYSTEM_ADMIN always gets the full catalog, including any permission added
# to it after this map was last updated (see _seed_role_permissions below).
# The other four are deliberately conservative rather than guessed generously:
# nothing here grants roles.manage, settings.manage, or backup.manage outside
# SYSTEM_ADMIN, since those control the permission system and company data
# itself. USER (a rank-and-file employee login) only gets dashboard.view -
# this system has no employee-self-service scoping yet (every other screen
# operates at full company scope, not "this user's own records"), so any
# broader grant for that role would either be inert or overpowered until
# that feature exists.
_DEFAULT_ROLE_PERMISSIONS: dict[UserRole, frozenset[str]] = {
    UserRole.MANAGER: frozenset(
        {
            "dashboard.view",
            "employees.view", "employees.manage",
            "departments.view", "departments.manage",
            "branches.view", "branches.manage",
            "attendance.view", "attendance.manage",
            "devices.view", "devices.manage",
            "reports.view", "reports.export",
            "users.view", "users.manage",
            "shifts.view", "shifts.manage",
            "holidays.view", "holidays.manage",
            "leave.view", "leave.manage",
            "settings.view",
            "payroll.view", "payroll.manage_rules", "payroll.manage_adjustments",
            "payroll.finalize", "payroll.reopen",
        }
    ),
    UserRole.HR: frozenset(
        {
            "dashboard.view",
            "employees.view", "employees.manage",
            "departments.view",
            "branches.view",
            "attendance.view", "attendance.manage",
            "shifts.view", "shifts.manage",
            "holidays.view", "holidays.manage",
            "leave.view", "leave.manage",
            "reports.view", "reports.export",
            "users.view",
            "payroll.view", "payroll.manage_adjustments",
        }
    ),
    UserRole.SUPERVISOR: frozenset(
        {
            "dashboard.view",
            "employees.view",
            "departments.view",
            "branches.view",
            "attendance.view", "attendance.manage",
            "devices.view",
            "shifts.view",
            "holidays.view",
            "leave.view", "leave.manage",
            "reports.view", "reports.export",
        }
    ),
    UserRole.USER: frozenset({"dashboard.view"}),
}

_UPDATABLE_COMPANY_FIELDS = frozenset(
    {"name", "legal_name", "logo_path", "email", "phone", "address", "tax_number", "is_active"}
)


class CompanyValidationError(Exception):
    """Raised when company input fails validation or a uniqueness check."""


class CompanyService:
    """Company (tenant) onboarding and administration.

    Unlike every other service in this project, this one is not
    constructed with a fixed ``company_id`` — it operates *across*
    companies (creating new ones, listing all of them), an operation
    only a platform-level administrator should be able to perform.

    Attributes:
        session: The active database session.
        actor_user_id: The user performing these operations, recorded
            on every audit log entry.
    """

    def __init__(self, session: Session, *, actor_user_id: int | None = None) -> None:
        """Create a company service.

        Args:
            session: The active database session.
            actor_user_id: The acting user's id, for audit attribution.
        """
        self.session = session
        self.actor_user_id = actor_user_id
        self.company_repo = CompanyRepository(session)
        self.audit_repo = AuditLogRepository(session)

    def create_company(
        self,
        *,
        name: str,
        legal_name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        address: str | None = None,
        tax_number: str | None = None,
    ) -> Company:
        """Onboard a new company.

        Creates the ``Company`` row, seeds default
        :class:`~models.company_settings.CompanySettings`, and creates
        this company's own copy of the five built-in roles — all in one
        call, so a freshly onboarded company can immediately create its
        first user against a real role.

        Args:
            name: Company display name; unique across the installation.
            legal_name: Optional full legal/registered name.
            email: Contact email.
            phone: Contact phone number.
            address: Postal/street address.
            tax_number: Tax or commercial registration number.

        Returns:
            The newly created, fully-seeded company.

        Raises:
            CompanyValidationError: If ``name`` fails length validation
                or is already in use.
        """
        if not is_within_length(name, minimum=2, maximum=200):
            raise CompanyValidationError("Company name must be 2-200 characters.")
        if self.company_repo.get_by_name(name) is not None:
            raise CompanyValidationError(f"Company name {name!r} is already in use.")

        company = Company(
            name=name,
            legal_name=legal_name,
            email=email,
            phone=phone,
            address=address,
            tax_number=tax_number,
        )
        self.company_repo.add(company)

        CompanySettingsRepository(self.session, company_id=company.id).get_or_create()

        # All permissions currently in the catalog, keyed by code, so
        # SYSTEM_ADMIN's grant always tracks the live catalog (including any
        # permission added after _DEFAULT_ROLE_PERMISSIONS was last updated)
        # rather than a snapshot that would need editing every time a new
        # module ships.
        all_permissions = PermissionRepository(self.session).list_all()

        role_repo = RoleRepository(self.session, company_id=company.id)
        for role_kind in UserRole:
            if role_kind is UserRole.SYSTEM_ADMIN:
                granted = all_permissions
            else:
                codes = _DEFAULT_ROLE_PERMISSIONS.get(role_kind, frozenset())
                granted = [permission for permission in all_permissions if permission.code in codes]
            role_repo.add(
                Role(
                    company_id=company.id,
                    name=role_kind.label_ar,
                    code=role_kind.value,
                    is_system_role=True,
                    permissions=granted,
                )
            )

        self.audit_repo.add(
            AuditLog(
                company_id=company.id,
                user_id=self.actor_user_id,
                action=AuditAction.CREATE,
                entity_type="Company",
                entity_id=company.id,
                description=f"Company {name!r} onboarded with default settings and roles.",
            )
        )
        return company

    def update_company_info(self, company: Company, **fields: Any) -> Company:
        """Update a company's profile fields.

        Args:
            company: The company to update.
            **fields: Any subset of ``name``/``legal_name``/
                ``logo_path``/``email``/``phone``/``address``/
                ``tax_number``/``is_active``.

        Returns:
            The updated company.

        Raises:
            CompanyValidationError: If a provided ``name`` fails length
                validation or collides with a different company.
        """
        if "name" in fields:
            if not is_within_length(str(fields["name"]), minimum=2, maximum=200):
                raise CompanyValidationError("Company name must be 2-200 characters.")
            existing = self.company_repo.get_by_name(str(fields["name"]))
            if existing is not None and existing.id != company.id:
                raise CompanyValidationError(
                    f"Company name {fields['name']!r} is already in use."
                )

        company.update_from_dict(fields, allowed_fields=_UPDATABLE_COMPANY_FIELDS)
        self.session.flush()

        self.audit_repo.add(
            AuditLog(
                company_id=company.id,
                user_id=self.actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="Company",
                entity_id=company.id,
                description=f"Updated company {company.name!r} profile.",
                changes={key: str(value) for key, value in fields.items()},
            )
        )
        return company

    def list_companies(self, *, active_only: bool = False) -> list[Company]:
        """List every company in this installation.

        Args:
            active_only: Restrict to active companies.

        Returns:
            Matching companies.
        """
        if active_only:
            return self.company_repo.list_active()
        return self.company_repo.list_all()

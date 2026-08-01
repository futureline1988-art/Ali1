"""Settings controller: bridges the settings screen to company info,
:class:`~models.company_settings.CompanySettings`, and backup/restore.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal
from sqlalchemy.orm import Session

from controllers.base_controller import BaseController
from models.company import Company
from models.company_settings import CompanySettings
from repositories.company_repository import CompanyRepository
from repositories.company_settings_repository import CompanySettingsRepository
from services.backup_service import BackupService
from services.company_service import CompanyService


def _company_to_dict(company: Company) -> dict[str, Any]:
    """Serialize a company's profile fields."""
    return company.to_dict()


def _settings_to_dict(settings: CompanySettings) -> dict[str, Any]:
    """Serialize a company's preference fields."""
    return settings.to_dict()


class SettingsController(BaseController):
    """Controller for the company profile, preferences, and backup screen."""

    company_info_changed = Signal()
    """Emitted after a successful company profile update."""

    settings_changed = Signal()
    """Emitted after a successful preferences update."""

    def __init__(
        self,
        *,
        company_id: int,
        actor_user_id: int | None = None,
        backup_service: BackupService | None = None,
    ) -> None:
        """Create a settings controller.

        Args:
            company_id: The company to scope every operation to.
            actor_user_id: The current user, for audit attribution.
            backup_service: Custom backup service, primarily for
                injecting a fake in tests; defaults to a new
                :class:`~services.backup_service.BackupService`.
        """
        super().__init__(company_id=company_id, actor_user_id=actor_user_id)
        self._backup_service = backup_service or BackupService()

    def get_company_info(self) -> dict[str, Any] | None:
        """Fetch this company's profile.

        Returns:
            The company's data as a dict, or ``None`` on failure.
        """

        def do_get(session: Session) -> dict[str, Any] | None:
            repo = CompanyRepository(session)
            company = repo.get_by_id(self.company_id)
            return _company_to_dict(company) if company is not None else None

        return self._run(do_get)

    def update_company_info(self, **fields: Any) -> dict[str, Any] | None:
        """Update this company's profile fields.

        Args:
            **fields: See
                :meth:`~services.company_service.CompanyService.update_company_info`.

        Returns:
            The updated company's data as a dict, or ``None`` on
            failure.
        """

        def do_update(session: Session) -> dict[str, Any]:
            repo = CompanyRepository(session)
            company = repo.get_by_id(self.company_id)
            if company is None:
                raise ValueError(f"Company {self.company_id!r} was not found.")
            service = CompanyService(session, actor_user_id=self.actor_user_id)
            updated = service.update_company_info(company, **fields)
            return _company_to_dict(updated)

        result = self._run(do_update)
        if result is not None:
            self.company_info_changed.emit()
        return result

    def get_settings(self) -> dict[str, Any] | None:
        """Fetch this company's preferences, seeding defaults if needed.

        Returns:
            The settings' data as a dict, or ``None`` on failure.
        """

        def do_get(session: Session) -> dict[str, Any]:
            repo = CompanySettingsRepository(session, company_id=self.company_id)
            settings = repo.get_or_create()
            return _settings_to_dict(settings)

        return self._run(do_get)

    def update_settings(self, **fields: Any) -> dict[str, Any] | None:
        """Update this company's preferences.

        Args:
            **fields: Any subset of
                :class:`~models.company_settings.CompanySettings`'s
                columns (e.g. ``language``, ``theme``, ``timezone_name``).

        Returns:
            The updated settings' data as a dict, or ``None`` on
            failure.
        """

        def do_update(session: Session) -> dict[str, Any]:
            repo = CompanySettingsRepository(session, company_id=self.company_id)
            settings = repo.get_or_create()
            # No explicit allowed_fields: relies on update_from_dict()'s own
            # default protected-field filtering (id/company_id/created_at/
            # version/...). Passing allowed_fields here would override that
            # protection entirely, per its documented contract.
            settings.update_from_dict(fields)
            session.flush()
            return _settings_to_dict(settings)

        result = self._run(do_update)
        if result is not None:
            self.settings_changed.emit()
        return result

    def create_backup(self, *, label: str | None = None) -> str | None:
        """Create a database backup.

        Args:
            label: Optional short label for the backup filename.

        Returns:
            The backup file path as a string, or ``None`` on failure.
        """
        try:
            path = self._backup_service.create_backup(label=label)
            return str(path)
        except Exception as exc:  # noqa: BLE001 - UI error boundary
            self._logger.error("Backup creation failed: {error}", error=str(exc))
            self.operation_failed.emit(str(exc))
            return None

    def restore_backup(self, backup_path: Path) -> bool:
        """Restore the database from a backup file.

        Args:
            backup_path: The backup to restore from.

        Returns:
            ``True`` on success, ``False`` on failure.
        """
        try:
            self._backup_service.restore_backup(backup_path)
            return True
        except Exception as exc:  # noqa: BLE001 - UI error boundary
            self._logger.error("Backup restore failed: {error}", error=str(exc))
            self.operation_failed.emit(str(exc))
            return False

    def list_backups(self) -> list[str]:
        """List existing backup files, most recent first.

        Returns:
            Backup file paths as strings; an empty list on failure.
        """
        try:
            return [str(path) for path in self._backup_service.list_backups()]
        except Exception as exc:  # noqa: BLE001 - UI error boundary
            self._logger.error("Listing backups failed: {error}", error=str(exc))
            self.operation_failed.emit(str(exc))
            return []

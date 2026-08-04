"""Data access for :class:`~developer_suite.models.configuration_publication.ConfigurationPublication`.

Read/append only — there is deliberately no ``update``/``delete``
method here (beyond the generic soft-delete every
:class:`~developer_suite.database.base.DeveloperSuiteBaseModel`
inherits, unused by any caller in this feature): publish history rows
are never mutated, per that model's own docstring.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from developer_suite.models.configuration_publication import ConfigurationPublication
from developer_suite.repositories.base_repository import BaseRepository


class ConfigurationPublicationRepository(BaseRepository[ConfigurationPublication]):
    """Data access for the configuration publish history, bound to one session."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        super().__init__(session, model=ConfigurationPublication)

    def _with_relationships(self, statement):
        return statement.options(
            joinedload(ConfigurationPublication.customer),
            joinedload(ConfigurationPublication.remote_configuration),
        )

    def get_current_for_device(self, target_device_public_id: str) -> ConfigurationPublication | None:
        """The most recently published (highest-version) row for one installation.

        Args:
            target_device_public_id: The receiving installation's
                device UUID.

        Returns:
            The current publication, or ``None`` if this installation
            has never received a publish.
        """
        statement = self._with_relationships(
            select(ConfigurationPublication)
            .where(
                ConfigurationPublication.target_device_public_id == target_device_public_id,
                ConfigurationPublication.is_deleted.is_(False),
            )
            .order_by(ConfigurationPublication.version.desc())
            .limit(1)
        )
        return self.session.execute(statement).unique().scalar_one_or_none()

    def list_history_for_device(self, target_device_public_id: str) -> list[ConfigurationPublication]:
        """Every publication ever sent to one installation, most recent first.

        Args:
            target_device_public_id: The receiving installation's
                device UUID.
        """
        statement = self._with_relationships(
            select(ConfigurationPublication)
            .where(
                ConfigurationPublication.target_device_public_id == target_device_public_id,
                ConfigurationPublication.is_deleted.is_(False),
            )
            .order_by(ConfigurationPublication.version.desc())
        )
        return list(self.session.execute(statement).unique().scalars().all())

    def list_all_history(self, *, limit: int | None = None) -> list[ConfigurationPublication]:
        """Every publication ever made, across every installation, most recent first.

        Used only by the Reporting & Analytics module's Configuration
        Publication History report (Phase 15) —
        :meth:`list_history_for_device` remains the one used by the
        actual publish/rollback UI, which is always scoped to one
        device.

        Args:
            limit: Cap the number of rows returned; ``None`` for every
                row.
        """
        statement = self._with_relationships(
            select(ConfigurationPublication)
            .where(ConfigurationPublication.is_deleted.is_(False))
            .order_by(ConfigurationPublication.created_at.desc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list(self.session.execute(statement).unique().scalars().all())

    def get_by_id_with_relationships(self, publication_id: int) -> ConfigurationPublication | None:
        """Fetch a single publication by id, with :attr:`customer`/:attr:`remote_configuration` loaded."""
        statement = self._with_relationships(
            select(ConfigurationPublication).where(ConfigurationPublication.id == publication_id)
        )
        return self.session.execute(statement).unique().scalar_one_or_none()

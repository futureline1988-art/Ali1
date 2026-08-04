"""Publish, compare, and roll back Remote Configuration bundles (Phase 13).

Sits directly on top of already-existing pieces, adding no duplicated
logic of its own:

* :class:`~developer_suite.repositories.configuration_repository.ConfigurationRepository`
  and :class:`~developer_suite.repositories.customer_repository.CustomerRepository`
  supply the draft bundle and the target customer, reused unmodified.
* :func:`~developer_suite.sync.configuration_sync.build_payload`/
  :func:`~developer_suite.sync.configuration_sync.compute_payload_checksum`
  are the single source of truth for what gets published and how it is
  checksummed.
* :class:`~developer_suite.repositories.sync_repository.SyncOutboxRepository`/
  :class:`~developer_suite.repositories.sync_repository.SyncEntityVersionRepository`
  are the exact same outbox-enqueue mechanism
  :meth:`~developer_suite.services.customer_service.CustomerService._enqueue_sync`
  already established in Phase 8 — publishing *is* enqueuing one
  ``company_configuration`` outbox entry, nothing more.

"Only administrators can publish configuration" is enforced by every
method here requiring a non-blank ``published_by`` — the Developer
Suite's own login gate (Phase 11: no module is reachable without an
authenticated admin session) is what guarantees a caller always has
one to supply; this service does not re-implement that check, only
refuses to silently accept a missing one.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from developer_suite.models.configuration_publication import ConfigurationPublication
from developer_suite.models.sync_state import SyncOperation
from developer_suite.repositories.configuration_publication_repository import (
    ConfigurationPublicationRepository,
)
from developer_suite.repositories.configuration_repository import ConfigurationRepository
from developer_suite.repositories.customer_repository import CustomerRepository
from developer_suite.repositories.sync_repository import SyncEntityVersionRepository, SyncOutboxRepository
from developer_suite.services.base_service import BaseService
from developer_suite.sync.configuration_sync import ENTITY_TYPE, build_payload, compute_payload_checksum
from utils.validators import is_within_length


class ConfigurationPublishServiceError(Exception):
    """Base class for publish/rollback failures the UI should display."""


class ConfigurationBundleNotFoundError(ConfigurationPublishServiceError):
    """No :class:`~developer_suite.models.remote_configuration.RemoteConfiguration` exists with the given id."""


class ConfigurationCustomerNotFoundError(ConfigurationPublishServiceError):
    """No :class:`~developer_suite.models.customer.Customer` exists with the given id."""


class ConfigurationPublicationNotFoundError(ConfigurationPublishServiceError):
    """No publication exists with the given id (or it was not sent to the given device)."""


class NotAnAdministratorError(ConfigurationPublishServiceError):
    """No ``published_by`` was supplied — see this module's own docstring."""


def _require_published_by(published_by: str) -> str:
    if not is_within_length(published_by, minimum=1, maximum=100):
        raise NotAnAdministratorError(
            "Publishing configuration requires an authenticated administrator."
        )
    return published_by


def _flatten(payload: dict, *, prefix: str = "") -> dict[str, object]:
    """Flatten a nested configuration payload into dotted-path leaf values.

    ``{"theme": {"mode": "light"}}`` becomes ``{"theme.mode": "light"}``
    — makes comparing two payloads a flat dict diff regardless of how
    deeply :func:`~developer_suite.sync.configuration_sync.build_payload`
    nests any given field.
    """
    flat: dict[str, object] = {}
    for key, value in payload.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, prefix=f"{path}."))
        else:
            flat[path] = value
    return flat


class ConfigurationPublishService(BaseService):
    """Publish a configuration bundle to one installation, and manage its history."""

    def publish(
        self,
        remote_configuration_id: int,
        *,
        customer_id: int,
        target_device_public_id: str,
        published_by: str,
        change_summary: str | None = None,
    ) -> ConfigurationPublication:
        """Publish a configuration bundle to one Attendance Client installation.

        Builds a self-contained snapshot from ``remote_configuration_id``'s
        current (live, possibly still-being-edited) field values and
        ``customer_id``'s company information, records it as a new,
        immutable :class:`~developer_suite.models.configuration_publication.ConfigurationPublication`
        version, and enqueues delivery through the existing generic
        sync outbox — reusing
        :class:`~developer_suite.sync.coordinator.SyncCoordinator`'s
        push loop unmodified for actual delivery.

        Args:
            remote_configuration_id: Which draft bundle to publish.
            customer_id: Whose company information to snapshot
                alongside it.
            target_device_public_id: The receiving installation's
                device UUID (see
                :attr:`~developer_suite.models.configuration_publication.ConfigurationPublication.target_device_public_id`'s
                own docstring for where this comes from).
            published_by: The authenticated admin's username.
            change_summary: A short note describing this publish.

        Returns:
            The newly created publication.

        Raises:
            NotAnAdministratorError: ``published_by`` is blank.
            ConfigurationBundleNotFoundError: No bundle with that id.
            ConfigurationCustomerNotFoundError: No customer with that id.
        """
        validated_published_by = _require_published_by(published_by)
        with self._session_scope() as session:
            bundle = ConfigurationRepository(session).get_configuration(remote_configuration_id)
            if bundle is None:
                raise ConfigurationBundleNotFoundError(
                    f"No configuration bundle with id={remote_configuration_id!r}."
                )
            customer = CustomerRepository(session).get_by_id(customer_id)
            if customer is None:
                raise ConfigurationCustomerNotFoundError(f"No customer with id={customer_id!r}.")

            payload = build_payload(customer, bundle)
            checksum = compute_payload_checksum(payload)

            publication_repo = ConfigurationPublicationRepository(session)
            current = publication_repo.get_current_for_device(target_device_public_id)
            next_version = current.version + 1 if current is not None else 1
            operation = SyncOperation.UPDATE if current is not None else SyncOperation.CREATE

            publication = ConfigurationPublication(
                remote_configuration_id=bundle.id,
                customer_id=customer.id,
                target_device_public_id=target_device_public_id,
                version=next_version,
                published_by=validated_published_by,
                change_summary=change_summary,
                checksum=checksum,
                payload=payload,
            )
            publication_repo.add(publication)
            self._enqueue_delivery(
                session,
                target_device_public_id=target_device_public_id,
                operation=operation,
                payload=payload,
                checksum=checksum,
            )
            return publication

    def rollback(
        self,
        target_device_public_id: str,
        *,
        to_publication_id: int,
        published_by: str,
        change_summary: str | None = None,
    ) -> ConfigurationPublication:
        """Roll back one installation to a previous configuration version.

        Creates a *new* publication carrying the old snapshot's exact
        payload/checksum — never deletes or rewrites the version being
        rolled back to, or anything published after it (see
        :class:`~developer_suite.models.configuration_publication.ConfigurationPublication`'s
        own docstring on why this table is append-only).

        Args:
            target_device_public_id: The installation to roll back.
            to_publication_id: Which past publication's payload to
                re-publish; must itself have been sent to
                ``target_device_public_id``.
            published_by: The authenticated admin's username.
            change_summary: Overrides the auto-generated
                "Rollback to version N" note.

        Returns:
            The newly created (rollback) publication.

        Raises:
            NotAnAdministratorError: ``published_by`` is blank.
            ConfigurationPublicationNotFoundError: No such publication,
                or it was not sent to ``target_device_public_id``.
        """
        validated_published_by = _require_published_by(published_by)
        with self._session_scope() as session:
            publication_repo = ConfigurationPublicationRepository(session)
            source = publication_repo.get_by_id_with_relationships(to_publication_id)
            if source is None or source.target_device_public_id != target_device_public_id:
                raise ConfigurationPublicationNotFoundError(
                    f"No publication with id={to_publication_id!r} for device "
                    f"{target_device_public_id!r}."
                )

            current = publication_repo.get_current_for_device(target_device_public_id)
            next_version = current.version + 1 if current is not None else 1
            summary = change_summary or f"Rollback to version {source.version}."

            rollback_publication = ConfigurationPublication(
                remote_configuration_id=source.remote_configuration_id,
                customer_id=source.customer_id,
                target_device_public_id=target_device_public_id,
                version=next_version,
                published_by=validated_published_by,
                change_summary=summary,
                checksum=source.checksum,
                payload=dict(source.payload),
            )
            publication_repo.add(rollback_publication)
            self._enqueue_delivery(
                session,
                target_device_public_id=target_device_public_id,
                operation=SyncOperation.UPDATE,
                payload=source.payload,
                checksum=source.checksum,
            )
            return rollback_publication

    def compare_pending_changes(
        self, remote_configuration_id: int, *, customer_id: int, target_device_public_id: str
    ) -> dict[str, tuple[object, object]]:
        """Diff a draft bundle's live values against what was last published.

        Args:
            remote_configuration_id: The draft bundle to compute
                "what would be published right now" from.
            customer_id: Whose company information to include in that
                computation.
            target_device_public_id: Whose current publication to
                compare against (an empty/never-published installation
                compares against an all-``None`` baseline, so every
                field shows as "pending").

        Returns:
            ``{dotted_field_path: (published_value, draft_value)}`` for
            every field whose value differs; empty if the draft is
            identical to what is currently published.

        Raises:
            ConfigurationBundleNotFoundError: No bundle with that id.
            ConfigurationCustomerNotFoundError: No customer with that id.
        """
        with self._session_scope() as session:
            bundle = ConfigurationRepository(session).get_configuration(remote_configuration_id)
            if bundle is None:
                raise ConfigurationBundleNotFoundError(
                    f"No configuration bundle with id={remote_configuration_id!r}."
                )
            customer = CustomerRepository(session).get_by_id(customer_id)
            if customer is None:
                raise ConfigurationCustomerNotFoundError(f"No customer with id={customer_id!r}.")

            draft_payload = build_payload(customer, bundle)
            current = ConfigurationPublicationRepository(session).get_current_for_device(
                target_device_public_id
            )
            published_payload = current.payload if current is not None else {}

        published_flat = _flatten(published_payload)
        draft_flat = _flatten(draft_payload)
        differences: dict[str, tuple[object, object]] = {}
        for key in sorted(set(published_flat) | set(draft_flat)):
            old_value = published_flat.get(key)
            new_value = draft_flat.get(key)
            if old_value != new_value:
                differences[key] = (old_value, new_value)
        return differences

    def get_current_publication(self, target_device_public_id: str) -> ConfigurationPublication | None:
        """The installation's currently published (highest-version) configuration, if any."""
        with self._session_scope() as session:
            return ConfigurationPublicationRepository(session).get_current_for_device(
                target_device_public_id
            )

    def list_publication_history(self, target_device_public_id: str) -> list[ConfigurationPublication]:
        """Every configuration ever published to one installation, most recent first."""
        with self._session_scope() as session:
            return ConfigurationPublicationRepository(session).list_history_for_device(
                target_device_public_id
            )

    def _enqueue_delivery(
        self,
        session: Session,
        *,
        target_device_public_id: str,
        operation: SyncOperation,
        payload: dict,
        checksum: str,
    ) -> None:
        """Queue one outbox entry for delivery, in the caller's own transaction.

        Mirrors :meth:`~developer_suite.services.customer_service.CustomerService._enqueue_sync`
        exactly, including why it must run in the same session/transaction
        as the business write it accompanies (see that method's own
        docstring) — the only difference is the entity type and that
        ``entity_id`` addresses a device rather than the entity's own
        public id (see
        :mod:`developer_suite.sync.configuration_sync`'s module
        docstring for why).
        """
        known_version = SyncEntityVersionRepository(session).get_known_version(
            ENTITY_TYPE, target_device_public_id
        )
        SyncOutboxRepository(session).enqueue(
            entity_type=ENTITY_TYPE,
            entity_id=target_device_public_id,
            operation=operation,
            payload=payload,
            checksum=checksum,
            base_version=known_version,
        )

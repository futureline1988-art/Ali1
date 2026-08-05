"""Create and fetch a subscription's initial Company Administrator credential.

The Developer Suite is the only caller of :meth:`InitialAdminService.set_initial_admin`
(see :mod:`server.api.routers.subscriptions`'s admin-scoped endpoint) —
the Attendance Client must never create the first administrator for a
company; it only ever *downloads* one already created here (see
:meth:`InitialAdminService.get_for_subscription`, called from the
device-facing endpoint).
"""

from __future__ import annotations

from database.database import Database
from server.config import ServerConfig
from server.models.initial_admin import InitialAdminAccount
from server.repositories.initial_admin_repository import InitialAdminRepository
from server.repositories.subscription_repository import SubscriptionRepository
from server.services.base_service import BaseService
from utils.security import hash_password, validate_password_strength


class InitialAdminServiceError(Exception):
    """Base class for initial-administrator operation failures the API layer should translate."""


class SubscriptionNotFoundForInitialAdminError(InitialAdminServiceError):
    """No subscription exists with the given id."""


class InitialAdminPasswordPolicyError(InitialAdminServiceError):
    """The given password fails :func:`utils.security.validate_password_strength`."""


class InitialAdminService(BaseService):
    """Create and fetch a subscription's initial Company Administrator."""

    def __init__(self, database: Database, *, config: ServerConfig) -> None:
        """Create a service bound to ``database`` and this server's configuration.

        Args:
            database: This server's own database.
            config: This server's configuration; supplies the bcrypt
                cost factor for hashing the administrator's password.
        """
        super().__init__(database)
        self._config = config

    def set_initial_admin(
        self, subscription_id: int, *, username: str, full_name: str, password: str
    ) -> InitialAdminAccount:
        """Create or replace the initial administrator for a subscription.

        The password is hashed immediately and only the hash is ever
        stored -- see :mod:`server.models.initial_admin`'s own
        docstring for why.

        Args:
            subscription_id: The subscription this administrator
                belongs to.
            username: The administrator's login handle.
            full_name: Display name.
            password: Plaintext password, hashed before storage.

        Returns:
            The created/updated row.

        Raises:
            SubscriptionNotFoundForInitialAdminError: No subscription
                with that id.
            InitialAdminPasswordPolicyError: ``password`` fails
                :func:`utils.security.validate_password_strength`.
        """
        violations = validate_password_strength(
            password, minimum_length=self._config.security.minimum_password_length
        )
        if violations:
            raise InitialAdminPasswordPolicyError("; ".join(violations))
        with self._session_scope() as session:
            if SubscriptionRepository(session).get_by_id(subscription_id) is None:
                raise SubscriptionNotFoundForInitialAdminError(
                    f"No subscription with id={subscription_id!r}."
                )
            repo = InitialAdminRepository(session)
            existing = repo.get_by_subscription_id(subscription_id)
            password_hash = hash_password(password, rounds=self._config.security.bcrypt_rounds)
            if existing is None:
                account = InitialAdminAccount(
                    subscription_id=subscription_id,
                    username=username,
                    full_name=full_name,
                    password_hash=password_hash,
                )
                return repo.add(account)
            existing.username = username
            existing.full_name = full_name
            existing.password_hash = password_hash
            session.flush()
            return existing

    def get_for_subscription(self, subscription_id: int) -> InitialAdminAccount | None:
        """Fetch the pending initial administrator for a subscription, or ``None``."""
        with self._session_scope() as session:
            return InitialAdminRepository(session).get_by_subscription_id(subscription_id)

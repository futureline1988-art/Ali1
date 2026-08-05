"""Device registration and authentication.

Reuses :func:`utils.security.hash_password`/:func:`utils.security.verify_password`
(bcrypt) and :func:`utils.security.generate_session_token` (a
cryptographically secure random string) directly rather than
reimplementing credential hashing or random generation — the same
primitives the Attendance Client already uses for user passwords and
session tokens, applied here to a device's sync credential instead.
Every call to :func:`~utils.security.hash_password` passes this
server's own :attr:`~server.config.ServerConfig.security` settings
explicitly, never falling back to :func:`config.get_config`'s bcrypt
cost factor.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from utils.security import generate_session_token, hash_password, verify_password

from database.database import Database
from server.config import ServerConfig
from server.models.device import SyncDevice, DeviceType
from server.repositories.device_repository import DeviceRepository
from server.repositories.subscription_repository import SubscriptionRepository
from server.services.base_service import BaseService


class DeviceServiceError(Exception):
    """Base class for device operation failures the API layer should translate."""


class DeviceNotFoundError(DeviceServiceError):
    """No device exists with the given id."""


class SubscriptionRequiredError(DeviceServiceError):
    """An Attendance Client tried to register with no matching, existing subscription.

    Registration for :attr:`~server.models.device.DeviceType.ATTENDANCE_CLIENT`
    always requires the Developer Suite to have already created a
    :class:`~server.models.subscription.Subscription` for the given
    company name — see :meth:`DeviceService.register_device`.
    """


class MaxDevicesReachedError(DeviceServiceError):
    """The company's subscription has already reached its device cap."""


class SubscriptionNotActiveError(DeviceServiceError):
    """The matched subscription exists but is suspended or expired.

    Only raised by :meth:`DeviceService.self_register_device` — the
    fully-automatic, no-admin-token onboarding path must not let a new
    device join a subscription that is not currently in good standing;
    :meth:`DeviceService.register_device` (the admin-driven path) is
    unaffected, matching its existing, deliberately looser contract.
    """


class DeviceService(BaseService):
    """Register, authenticate, and manage devices."""

    def __init__(self, database: Database, *, config: ServerConfig) -> None:
        """Create a device service bound to ``database`` and this server's configuration.

        Args:
            database: This server's own database.
            config: This server's configuration; supplies the bcrypt
                cost factor for hashing device credentials.
        """
        super().__init__(database)
        self._config = config

    def register_device(
        self, *, name: str, device_type: DeviceType, company_name: str | None = None
    ) -> tuple[SyncDevice, str]:
        """Register a new device and issue its one-time sync credential.

        Args:
            name: A human-readable label for this device.
            device_type: Which application this device is an
                installation of.
            company_name: The real Attendance Client enrollment flow
                (:meth:`~sync.coordinator.ClientSyncCoordinator.enroll`)
                always supplies this — the exact
                :attr:`~server.models.subscription.Subscription.company_name`
                this installation belongs to, resolved to a subscription
                and linked via :attr:`~server.models.device.SyncDevice.subscription_id`,
                with capacity enforced against
                :attr:`~server.models.subscription.Subscription.max_devices`.
                Left ``None``, no subscription lookup/enforcement
                happens at all and the device registers exactly as it
                did before subscriptions existed (used by every
                registration that has nothing to do with subscription
                entitlement, e.g. this server's own sync/update test
                fixtures) — :attr:`~server.models.device.SyncDevice.subscription_id`
                simply stays unset. Always ignored for
                :attr:`~server.models.device.DeviceType.DEVELOPER_SUITE`
                (a vendor machine has no subscription).

        Returns:
            A ``(device, api_key)`` pair. ``api_key`` is the plaintext
            credential — returned here and only here; only its bcrypt
            hash is ever stored, so a caller that loses it must
            register a new device rather than recover the old key.

        Raises:
            SubscriptionRequiredError: ``company_name`` was given but
                no subscription exists for it.
            MaxDevicesReachedError: The matched subscription has
                already reached its
                :attr:`~server.models.subscription.Subscription.max_devices`
                cap.
        """
        api_key = generate_session_token()
        with self._session_scope() as session:
            subscription_id: int | None = None
            if device_type is DeviceType.ATTENDANCE_CLIENT and company_name is not None:
                subscription_repo = SubscriptionRepository(session)
                subscription = subscription_repo.get_by_company_name(company_name)
                if subscription is None:
                    raise SubscriptionRequiredError(
                        f"No subscription exists for company {company_name!r}. "
                        "Ask your vendor to create one before registering this installation."
                    )
                if subscription_repo.count_active_devices(subscription.id) >= subscription.max_devices:
                    raise MaxDevicesReachedError(
                        f"Company {company_name!r} has already reached its device limit "
                        f"({subscription.max_devices})."
                    )
                subscription_id = subscription.id

            device = SyncDevice(
                name=name,
                device_type=device_type,
                api_key_hash=hash_password(api_key, rounds=self._config.security.bcrypt_rounds),
                is_active=True,
                subscription_id=subscription_id,
            )
            DeviceRepository(session).add(device)
            return device, api_key

    def self_register_device(self, *, name: str, company_name: str) -> tuple[SyncDevice, str]:
        """Fully-automatic self-registration for a fresh Attendance Client installation.

        The no-administrator-action onboarding path: called with only
        this installation's configured ``company_name`` (see
        :meth:`~sync.coordinator.ClientSyncCoordinator.self_enroll`) —
        no bearer token, no prior manual linking in the Developer
        Suite. Always :attr:`~server.models.device.DeviceType.ATTENDANCE_CLIENT`;
        there is no self-service path for a Developer Suite
        installation, which always registers through
        :meth:`register_device` with an admin bearer token instead.

        Stricter than :meth:`register_device`'s admin-driven path in
        one way: the matched subscription must currently be *active*
        (not suspended, not expired) — a subscription not in good
        standing must not gain new devices, even automatically.

        Args:
            name: A human-readable label for this device.
            company_name: The exact
                :attr:`~server.models.subscription.Subscription.company_name`
                this installation belongs to.

        Returns:
            A ``(device, api_key)`` pair, exactly like :meth:`register_device`.

        Raises:
            SubscriptionRequiredError: No subscription exists for
                ``company_name``.
            SubscriptionNotActiveError: A subscription exists but is
                suspended or expired.
            MaxDevicesReachedError: The matched subscription has
                already reached its
                :attr:`~server.models.subscription.Subscription.max_devices`
                cap.
        """
        api_key = generate_session_token()
        with self._session_scope() as session:
            subscription_repo = SubscriptionRepository(session)
            subscription = subscription_repo.get_by_company_name(company_name)
            if subscription is None:
                raise SubscriptionRequiredError(
                    f"No subscription exists for company {company_name!r}. "
                    "Ask your vendor to create one before registering this installation."
                )
            if not subscription.is_active:
                raise SubscriptionNotActiveError(
                    f"Company {company_name!r}'s subscription is not active "
                    "(suspended or expired); new devices cannot register."
                )
            if subscription_repo.count_active_devices(subscription.id) >= subscription.max_devices:
                raise MaxDevicesReachedError(
                    f"Company {company_name!r} has already reached its device limit "
                    f"({subscription.max_devices})."
                )

            device = SyncDevice(
                name=name,
                device_type=DeviceType.ATTENDANCE_CLIENT,
                api_key_hash=hash_password(api_key, rounds=self._config.security.bcrypt_rounds),
                is_active=True,
                subscription_id=subscription.id,
            )
            DeviceRepository(session).add(device)
            return device, api_key

    def authenticate_device(self, public_id: uuid.UUID, api_key: str) -> SyncDevice | None:
        """Verify a device's credential and, on success, update its last-seen timestamp.

        Args:
            public_id: The device's public UUID (from the
                ``X-Device-Id`` header).
            api_key: The plaintext credential to verify (from the
                ``X-Device-Api-Key`` header).

        Returns:
            The authenticated device, or ``None`` if ``public_id`` is
            unknown, the device is inactive/deleted, or ``api_key``
            does not match.
        """
        with self._session_scope() as session:
            repo = DeviceRepository(session)
            device = repo.get_active_by_public_id(public_id)
            if device is None:
                return None
            if not verify_password(api_key, device.api_key_hash):
                return None
            device.last_seen_at = datetime.now(timezone.utc)
            session.flush()
            return device

    def deactivate_device(self, device_id: int) -> SyncDevice:
        """Revoke a device's credential without deleting its sync history.

        Args:
            device_id: The device to deactivate.

        Returns:
            The updated device.

        Raises:
            DeviceNotFoundError: No device exists with that id.
        """
        with self._session_scope() as session:
            device = DeviceRepository(session).get_by_id(device_id)
            if device is None:
                raise DeviceNotFoundError(f"No device with id={device_id!r}.")
            device.is_active = False
            session.flush()
            return device

    def list_devices(self) -> list[SyncDevice]:
        """List every registered, non-deleted device."""
        with self._session_scope() as session:
            return DeviceRepository(session).list_all()

    def get_device(self, device_id: int) -> SyncDevice | None:
        """Fetch a single device by id, or ``None`` if not found."""
        with self._session_scope() as session:
            return DeviceRepository(session).get_by_id(device_id)

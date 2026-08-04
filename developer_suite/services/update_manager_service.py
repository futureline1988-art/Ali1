"""Software update management: create, sign, upload, target, publish, and roll back.

Wraps :class:`~developer_suite.admin.client.AdminApiClient`'s Phase 14
write methods with the one piece of business logic that must never
happen on the Attendance Server: signing a package with the vendor's
private key (see :mod:`server.models.update`'s module docstring —
this server only stores and serves signatures, it never produces
one).

Deliberately a **separate** Ed25519 keypair from
:attr:`~developer_suite.config.DeveloperSuiteConfig.licensing_private_key_path`
(:attr:`~developer_suite.config.DeveloperSuiteConfig.update_signing_private_key_path`),
even though both use the exact same :mod:`licensing.crypto.signing`
primitives: a license key and an update package answer completely
different questions ("is this installation entitled to run" vs. "is
this executable genuinely from the vendor, byte-for-byte"), and
compromising one key must never let an attacker forge the other. This
also keeps this phase's explicit "do not change licensing" constraint
literal — nothing under ``licensing/keys.py`` or the license
verification path is touched; only the already-generic signing
primitives Phase 1 built for exactly this kind of vendor-side use are
reused.

Targeting "specific customers" or "customer groups" (see
:mod:`developer_suite.models.customer_group`) is resolved *in this
service* down to the flat list of device public ids the Attendance
Server's ``UpdateTarget`` rows actually store — this server has no
``Customer``-to-``SyncDevice`` link at all (see
:mod:`server.models.update`'s own docstring), the same limitation
:class:`~developer_suite.services.configuration_publish_service.ConfigurationPublishService`
already works within for Phase 13's device-targeted publishing. There
is therefore no way to resolve a customer to *the* device that
represents its installation automatically; :meth:`UpdateManagerService.suggest_devices_for_customers`
offers a best-effort, name-substring match as a starting point for the
UI to present, never a source of truth the admin cannot review and
adjust before confirming targeting.
"""

from __future__ import annotations

import hashlib
from base64 import b64encode
from datetime import datetime
from pathlib import Path

from typing import Callable, TypeVar

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from developer_suite.admin.client import (
    AdminApiClient,
    AdminApiError,
    DeviceInfo,
    UpdatePackageInfo,
    UpdateStatsInfo,
    UpdateVersionInfo,
)
from developer_suite.models.customer import Customer
from licensing.crypto.signing import SigningKeyError, ensure_keypair, sign_bytes

_T = TypeVar("_T")


class UpdateManagerServiceError(Exception):
    """Base class for update-management failures the UI should display.

    Every method on this service that talks to the Attendance Server
    (which is nearly all of them — unlike Phase 13's configuration
    publishing, which only enqueues a local outbox entry, every update
    -management operation here is a direct, immediate REST call) wraps
    any :class:`~developer_suite.admin.client.AdminApiError` the
    underlying :class:`~developer_suite.admin.client.AdminApiClient`
    raises into this one exception type — a caller (the Update Manager
    page) needs to catch only one thing, whether the failure was "no
    admin token yet," "server unreachable," or "server rejected the
    request."
    """


class UpdateSigningKeyError(UpdateManagerServiceError):
    """The update-signing private key exists but is invalid.

    A *missing* key no longer reaches the UI as an error at all — see
    :meth:`UpdateManagerService._load_private_key` — so this now only
    fires for a key file that is present but corrupt, mirroring
    :class:`~developer_suite.services.license_service.LicenseSigningKeyError`'s
    own reasoning: a UI layer should be able to catch one clear
    exception type without importing from ``licensing.crypto`` itself.
    """


class UpdateManagerService:
    """Create, sign, upload, target, publish, and roll back software updates."""

    def __init__(
        self,
        admin_client: AdminApiClient,
        *,
        private_key_path: Path,
        public_key_path: Path | None = None,
    ) -> None:
        """Create an update manager service bound to an admin client and signing key location.

        Args:
            admin_client: Performs every actual HTTP call against the
                Attendance Server's update-management endpoints.
            private_key_path: Where to load the vendor's Ed25519
                *update-signing* private key from (see
                :attr:`~developer_suite.config.DeveloperSuiteConfig.update_signing_private_key_path`
                — a separate key from the license-signing one).
                Auto-created here, once, the first time it's needed if
                nothing exists at this path yet — see
                :func:`licensing.crypto.signing.ensure_keypair`.
            public_key_path: Where to also write the matching public
                key if a new keypair is generated (see
                :attr:`~developer_suite.config.DeveloperSuiteConfig.update_signing_public_key_path`),
                purely so the vendor can retrieve it afterwards and
                embed it in the next Attendance Client build's
                ``updates/keys.py``. Optional; omit to skip writing it
                out.
        """
        self._admin_client = admin_client
        self._private_key_path = private_key_path
        self._public_key_path = public_key_path

    def _load_private_key(self) -> Ed25519PrivateKey:
        """Load the configured update-signing private key, generating it once if missing.

        Mirrors :meth:`~developer_suite.services.license_service.LicenseService._load_private_key`'s
        reasoning exactly, for the separate update-signing keypair:
        this machine is always the vendor's own (a customer's
        Attendance Client never holds or needs this key, only
        ``updates/keys.py``'s embedded public half), so a first call
        with nothing on disk yet bootstraps a fresh keypair rather than
        erroring. An existing key is always loaded as-is, never
        regenerated.

        Raises:
            UpdateSigningKeyError: A key file exists at the configured
                path but does not contain a valid Ed25519 private key.
        """
        try:
            return ensure_keypair(self._private_key_path, public_key_path=self._public_key_path)
        except SigningKeyError as exc:
            raise UpdateSigningKeyError(str(exc)) from exc

    @staticmethod
    def _call(operation: Callable[[], _T]) -> _T:
        """Invoke one :class:`~developer_suite.admin.client.AdminApiClient` call, translating its errors.

        Every public method below that reaches the Attendance Server
        routes through this, so a caller only ever needs to catch
        :class:`UpdateManagerServiceError` (see this module's own
        docstring for why that matters here specifically).
        """
        try:
            return operation()
        except AdminApiError as exc:
            raise UpdateManagerServiceError(str(exc)) from exc

    def create_version(
        self,
        *,
        version: str,
        release_notes: str | None,
        min_supported_version: str | None,
        update_type: str,
    ) -> UpdateVersionInfo:
        """Create a new, draft update version."""
        return self._call(
            lambda: self._admin_client.create_update_version(
                version=version,
                release_notes=release_notes,
                min_supported_version=min_supported_version,
                update_type=update_type,
            )
        )

    def upload_package(self, update_version_id: int, *, package_type: str, file_path: Path) -> UpdatePackageInfo:
        """Sign and upload a setup or portable package file.

        Args:
            update_version_id: Which version this package belongs to.
            package_type: ``"setup"`` or ``"portable"``.
            file_path: The package file on this machine to sign and
                upload.

        Raises:
            UpdateSigningKeyError: The update-signing private key
                exists but is invalid (a missing one is auto-created
                instead — see :meth:`_load_private_key`).
            UpdateManagerServiceError: The upload could not be
                completed (no admin token, unreachable server,
                checksum rejected, ...).
        """
        private_key = self._load_private_key()
        file_bytes = file_path.read_bytes()
        checksum = hashlib.sha256(file_bytes).hexdigest()
        signature = b64encode(sign_bytes(private_key, file_bytes)).decode("ascii")
        return self._call(
            lambda: self._admin_client.upload_update_package(
                update_version_id,
                package_type=package_type,
                file_bytes=file_bytes,
                checksum_sha256=checksum,
                signature_base64=signature,
                original_filename=file_path.name,
            )
        )

    def set_targets_all(self, update_version_id: int) -> None:
        """Target every registered Attendance Client installation."""
        self._call(lambda: self._admin_client.set_update_targets(update_version_id, scope="all"))

    def set_targets_devices(self, update_version_id: int, *, device_public_ids: list[str]) -> None:
        """Target only the given device public ids."""
        self._call(
            lambda: self._admin_client.set_update_targets(
                update_version_id, scope="device", device_public_ids=device_public_ids
            )
        )

    def publish(self, update_version_id: int) -> UpdateVersionInfo:
        """Publish a version immediately."""
        return self._call(lambda: self._admin_client.publish_update(update_version_id))

    def schedule(self, update_version_id: int, *, scheduled_at: datetime) -> UpdateVersionInfo:
        """Schedule a version to become available at a future time."""
        return self._call(lambda: self._admin_client.schedule_update(update_version_id, scheduled_at=scheduled_at))

    def disable(self, update_version_id: int) -> UpdateVersionInfo:
        """Disable a version, removing it from every latest/assigned response."""
        return self._call(lambda: self._admin_client.disable_update(update_version_id))

    def rollback(self, update_version_id: int, *, reason: str | None = None) -> UpdateVersionInfo:
        """Roll back a version: never deletes it, only excludes it from future offers."""
        return self._call(lambda: self._admin_client.rollback_update(update_version_id, reason=reason))

    def list_versions(self) -> list[UpdateVersionInfo]:
        """List every update version regardless of status, most recently created first."""
        return self._call(self._admin_client.list_update_versions)

    def get_version_detail(self, update_version_id: int) -> dict:
        """Fetch one version's full detail: metadata, packages, targets, and audit history."""
        return self._call(lambda: self._admin_client.get_update_version_detail(update_version_id))

    def get_stats(self) -> UpdateStatsInfo:
        """Fetch update-distribution statistics for the Developer Dashboard."""
        return self._call(self._admin_client.get_update_stats)

    def suggest_devices_for_customers(
        self, customers: list[Customer], *, registered_devices: list[DeviceInfo]
    ) -> list[str]:
        """Best-effort match of registered Attendance Client devices to selected customers.

        A device's own name is a free-text label an administrator
        chose at registration time — there is no real foreign key from
        :class:`~developer_suite.models.customer.Customer` to a
        registered device (see this module's own docstring). This
        returns every attendance-client device whose name contains any
        selected customer's company name as a case-insensitive
        substring — a starting point for the UI to pre-check, always
        reviewable and editable by the administrator before confirming
        targeting, never treated as authoritative.

        Args:
            customers: The selected customers (or every member of a
                selected customer group).
            registered_devices: Every currently registered device (see
                :meth:`~developer_suite.admin.client.AdminApiClient.list_devices`).

        Returns:
            The matched devices' public ids, deduplicated.
        """
        company_names = [c.company_name.strip().lower() for c in customers if c.company_name.strip()]
        matched: list[str] = []
        for device in registered_devices:
            if device.device_type != "attendance_client":
                continue
            device_name = device.name.lower()
            if any(name in device_name for name in company_names):
                matched.append(device.public_id)
        return matched

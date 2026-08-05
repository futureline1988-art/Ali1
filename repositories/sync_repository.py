"""Data access for the Attendance Client's local synchronization bookkeeping.

Two small repositories, one per table in :mod:`models.sync_state`,
mirroring :mod:`developer_suite.repositories.sync_repository`'s own
pull-side repositories exactly. Every method operates against a
caller-supplied :class:`~sqlalchemy.orm.Session`, like every other
repository in this package (see :mod:`repositories.base_repository`'s
docstring for why repositories never open their own session).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.sync_state import ClientSyncCredential, ClientSyncCursor


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ClientSyncCredentialRepository:
    """Data access for the singleton :class:`~models.sync_state.ClientSyncCredential` row."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        self.session = session

    def get(self) -> ClientSyncCredential | None:
        """Return this installation's stored credential, or ``None`` if never enrolled."""
        return self.session.execute(select(ClientSyncCredential)).scalars().first()

    def save(self, *, device_public_id: str, api_key: str, server_url: str) -> ClientSyncCredential:
        """Create or overwrite this installation's singleton credential row.

        Args:
            device_public_id: The device UUID issued at registration.
            api_key: The plaintext sync credential issued at
                registration; encrypted at rest by
                :class:`~models.encrypted_types.EncryptedString`.
            server_url: The Attendance Server base URL this credential
                belongs to.

        Returns:
            The saved credential row.
        """
        credential = self.get()
        if credential is None:
            credential = ClientSyncCredential(
                device_public_id=device_public_id,
                api_key=api_key,
                server_url=server_url,
                registered_at=_utc_now(),
            )
            self.session.add(credential)
        else:
            credential.device_public_id = device_public_id
            credential.api_key = api_key
            credential.server_url = server_url
            credential.registered_at = _utc_now()
        self.session.flush()
        return credential

    def get_bound_company_id(self) -> int | None:
        """Return this device's permanently bound local company id, or ``None`` if never bound.

        See :meth:`set_bound_company`.
        """
        credential = self.get()
        return credential.bound_company_id if credential is not None else None

    def set_bound_company(self, company_id: int) -> None:
        """Permanently bind this already-enrolled device to a local company.

        Called once, right after this device's first-ever successful
        login-driven self-registration completes (see
        :meth:`~services.subscription_check_service.SubscriptionCheckService.check_for_login`)
        — every future login skips the company picker (see
        :meth:`~ui.login_window.LoginWindow._populate_companies`) and
        every future subscription check assumes this company.

        Raises:
            RuntimeError: This device has not enrolled yet (no
                credential row exists to bind).
        """
        credential = self.get()
        if credential is None:
            raise RuntimeError(
                "Cannot bind a company before this installation has enrolled with the Attendance Server."
            )
        credential.bound_company_id = company_id
        self.session.flush()


class ClientSyncCursorRepository:
    """Data access for per-``entity_type`` pull cursors."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        self.session = session

    def get_cursor(self, entity_type: str) -> int:
        """Return the last pulled change id for ``entity_type`` (``0`` if never pulled)."""
        row = self.session.execute(
            select(ClientSyncCursor).where(ClientSyncCursor.entity_type == entity_type)
        ).scalar_one_or_none()
        return row.last_change_id if row is not None else 0

    def advance_cursor(self, entity_type: str, new_cursor: int) -> None:
        """Persist ``new_cursor`` as the last pulled change id for ``entity_type``.

        Args:
            entity_type: The synced entity type.
            new_cursor: The new cursor value; a no-op if it is not
                greater than the currently stored one, so an
                out-of-order or retried call can never move a cursor
                backwards.
        """
        row = self.session.execute(
            select(ClientSyncCursor).where(ClientSyncCursor.entity_type == entity_type)
        ).scalar_one_or_none()
        if row is None:
            self.session.add(ClientSyncCursor(entity_type=entity_type, last_change_id=new_cursor))
        elif new_cursor > row.last_change_id:
            row.last_change_id = new_cursor
        self.session.flush()

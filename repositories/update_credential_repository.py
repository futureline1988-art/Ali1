"""Data access for the Attendance Client's optional update-server credential.

Mirrors the shape of the retired ``repositories.sync_repository``'s
``ClientSyncCredentialRepository``, scoped down to just what
:mod:`updates.checker` needs (see :mod:`models.update_credential`'s
own docstring for why this table is now separate and optional).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.update_credential import UpdateServerCredential


class UpdateCredentialRepository:
    """Data access for the singleton :class:`~models.update_credential.UpdateServerCredential` row."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        self.session = session

    def get(self) -> UpdateServerCredential | None:
        """Return this installation's update-server credential, or ``None`` if never configured."""
        return self.session.execute(select(UpdateServerCredential)).scalars().first()

    def save(self, *, device_public_id: str, api_key: str, server_url: str) -> UpdateServerCredential:
        """Create or overwrite the singleton credential row."""
        row = self.get()
        if row is None:
            row = UpdateServerCredential(
                device_public_id=device_public_id, api_key=api_key, server_url=server_url
            )
            self.session.add(row)
        else:
            row.device_public_id = device_public_id
            row.api_key = api_key
            row.server_url = server_url
        self.session.flush()
        return row

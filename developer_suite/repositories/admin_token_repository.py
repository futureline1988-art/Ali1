"""Data access for the singleton :class:`~developer_suite.models.admin_token.AdminBootstrapToken` row.

Temporary, like the model it manages — see that model's own docstring.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from developer_suite.models.admin_token import AdminBootstrapToken


class AdminBootstrapTokenRepository:
    """Data access for this installation's bootstrap admin token."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        self.session = session

    def get(self) -> AdminBootstrapToken | None:
        """Return the stored bootstrap token row, or ``None`` if none has been saved yet."""
        return self.session.execute(select(AdminBootstrapToken)).scalars().first()

    def save(self, token: str) -> AdminBootstrapToken:
        """Create or overwrite the singleton bootstrap token row.

        Args:
            token: The plaintext ``sync:admin``-scoped bearer token to
                store (encrypted at rest by
                :class:`~developer_suite.models.encrypted_types.EncryptedString`).

        Returns:
            The saved row.
        """
        record = self.get()
        if record is None:
            record = AdminBootstrapToken(token=token, saved_at=datetime.now(timezone.utc))
            self.session.add(record)
        else:
            record.token = token
            record.saved_at = datetime.now(timezone.utc)
        self.session.flush()
        return record

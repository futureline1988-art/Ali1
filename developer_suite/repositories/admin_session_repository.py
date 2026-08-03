"""Data access for the singleton :class:`~developer_suite.models.admin_session.AdminSessionRecord` row.

Mirrors :class:`~developer_suite.repositories.admin_token_repository.AdminBootstrapTokenRepository`'s
Phase 10 shape (now removed — replaced by this module).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from developer_suite.models.admin_session import AdminSessionRecord


class AdminSessionRecordRepository:
    """Data access for this installation's remembered admin session."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        self.session = session

    def get(self) -> AdminSessionRecord | None:
        """Return the stored session row, or ``None`` if none has been saved yet."""
        return self.session.execute(select(AdminSessionRecord)).scalars().first()

    def save(self, *, username: str, refresh_token: str, remember_me: bool) -> AdminSessionRecord:
        """Create or overwrite the singleton session row.

        Args:
            username: The account this session belongs to.
            refresh_token: The plaintext refresh token to store
                (encrypted at rest by
                :class:`~developer_suite.models.encrypted_types.EncryptedString`).
            remember_me: Whether this session should survive an
                application restart.

        Returns:
            The saved row.
        """
        record = self.get()
        if record is None:
            record = AdminSessionRecord(
                username=username,
                refresh_token=refresh_token,
                remember_me=remember_me,
                saved_at=datetime.now(timezone.utc),
            )
            self.session.add(record)
        else:
            record.username = username
            record.refresh_token = refresh_token
            record.remember_me = remember_me
            record.saved_at = datetime.now(timezone.utc)
        self.session.flush()
        return record

    def clear(self) -> None:
        """Delete the stored session row, if any (a no-op if there is none)."""
        self.session.execute(delete(AdminSessionRecord))
        self.session.flush()

"""Data access for :class:`~server.models.admin_password_reset.AdminPasswordResetToken`."""

from __future__ import annotations

from sqlalchemy.orm import Session

from server.models.admin_password_reset import AdminPasswordResetToken
from server.repositories.base_repository import BaseRepository


class AdminPasswordResetRepository(BaseRepository[AdminPasswordResetToken]):
    """Data access for password reset tokens, bound to one session.

    Lookup by the token's public id is already covered by
    :meth:`~server.repositories.base_repository.BaseRepository.get_by_public_id`
    — nothing custom is needed beyond the generic CRUD this composes.
    """

    def __init__(self, session: Session) -> None:
        """Create a repository bound to ``session``."""
        super().__init__(session, model=AdminPasswordResetToken)

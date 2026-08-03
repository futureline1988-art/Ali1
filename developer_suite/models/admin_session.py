"""The Phase 11 persisted admin session record.

Replaces the Phase 10 ``AdminBootstrapToken`` bootstrap mechanism now
that real authentication exists (see
:mod:`developer_suite.admin.session_manager`) — this row is what makes
"remember me" possible: an encrypted refresh token this installation
can silently redeem for a new access token on the next launch, instead
of prompting for credentials every time. When "remember me" was not
selected at login, no row is ever written.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from developer_suite.database.base import Base
from developer_suite.models.encrypted_types import EncryptedString
from models.base import UTCDateTime


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AdminSessionRecord(Base):
    """A singleton row holding this installation's remembered admin session, if any.

    Exactly one row ever exists — the same "singleton identity row"
    shape :class:`~developer_suite.models.sync_state.SyncDeviceCredential`
    already uses for this installation's own sync device credential.

    Attributes:
        username: The account this session belongs to, kept only for
            display (e.g. "signed in as ...") — never used to
            authenticate anything by itself.
        refresh_token: The plaintext refresh token, encrypted at rest
            (see :mod:`developer_suite.models.encrypted_types`) — must
            be recoverable in plaintext to redeem it against
            ``POST /api/v1/auth/refresh``, so (like
            :attr:`~developer_suite.models.sync_state.SyncDeviceCredential.api_key`)
            it cannot be a one-way hash.
        remember_me: Whether this session should survive an application
            restart. Rows are only ever written when this is ``True``;
            see :mod:`developer_suite.admin.session_manager`.
        saved_at: When this row was last (re)written.
    """

    __tablename__ = "admin_session_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    refresh_token: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    remember_me: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    saved_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utc_now)

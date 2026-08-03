"""Password reset infrastructure: single-use, expiring reset tokens.

Deliberately *infrastructure only* — this model and the service
methods built on it (:meth:`~server.services.admin_auth_service.AdminAuthService.request_password_reset`/
:meth:`~server.services.admin_auth_service.AdminAuthService.complete_password_reset`)
let a caller mint and redeem a reset token; nothing here sends an
email or any other out-of-band delivery, since no such delivery
mechanism (SMTP client, notification service) exists anywhere in this
codebase, and Phase 11 does not add one. Delivering the token to the
account holder is left to whatever future phase actually needs it.

Same public-lookup-key / bcrypt-verified-secret split as
:class:`~server.models.admin_session.AdminSession` and
:class:`~server.models.device.SyncDevice`, for the same reason: the
issued token is ``f"{record.public_id}.{secret}"``, looked up by
``public_id`` and then bcrypt-verified.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import UTCDateTime
from server.database.base import ServerBaseModel


class AdminPasswordResetToken(ServerBaseModel):
    """One single-use password reset token issued for one admin account.

    Attributes:
        admin_account_id: Which account this token can reset the
            password for.
        token_hash: Bcrypt hash of the token's secret half.
        expires_at: When this token stops being redeemable.
        used_at: Set once this token has been redeemed; a used token
            can never be redeemed again even if not yet expired.
    """

    admin_account_id: Mapped[int] = mapped_column(
        ForeignKey("admin_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    @property
    def is_valid(self) -> bool:
        """Whether this token can currently be redeemed."""
        if self.used_at is not None:
            return False
        return self.expires_at > datetime.now(timezone.utc)

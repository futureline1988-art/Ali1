"""A subscription's initial Company Administrator credential.

The Attendance Client must never create the first administrator for a
company -- that account is created exclusively from the Developer
Suite, once, right after a subscription (and its Company Code) exist.
:class:`InitialAdminAccount` is what makes that credential available
for a fresh Attendance Client installation to *download* (see
:func:`~server.api.routers.subscriptions.get_initial_admin`, an
authenticated-device endpoint) and store as its own first local
``models.user.User`` row -- see :mod:`services.subscription_check_service`.

Deliberately holds only a bcrypt hash, never the plaintext password:
the Developer Suite hashes it immediately on creation (see
:meth:`~server.services.initial_admin_service.InitialAdminService.set_initial_admin`)
exactly like every other password in this system, and the Attendance
Client stores that same hash directly in its own local ``User.password_hash``
column without ever needing (or being sent) the plaintext.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from server.database.base import ServerBaseModel


class InitialAdminAccount(ServerBaseModel):
    """The pending/downloadable initial Company Administrator for one subscription.

    Exactly one row per subscription (re-setting replaces it in place
    -- see :meth:`~server.services.initial_admin_service.InitialAdminService.set_initial_admin`).
    Available for download by *any* device already linked to that
    subscription, not consumed on first fetch: each independent
    Attendance Client installation has its own local database and
    needs to bootstrap its own local admin account the same way.

    Attributes:
        subscription_id: The subscription this administrator belongs
            to; unique, so a subscription has at most one pending
            initial administrator at a time.
        username: The administrator's login handle -- becomes the
            downloaded local ``User.username``.
        full_name: Display name -- becomes the local ``User.full_name``.
        password_hash: A bcrypt hash, never the plaintext password.
    """

    subscription_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("subscriptions.id"), nullable=False, unique=True, index=True
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

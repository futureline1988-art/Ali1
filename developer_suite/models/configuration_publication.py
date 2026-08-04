"""Configuration publication ORM model — an append-only publish history row.

A :class:`ConfigurationPublication` records one moment a
:class:`~developer_suite.models.remote_configuration.RemoteConfiguration`
bundle (plus a chosen customer's own company information) was
published toward one registered Attendance Client installation. Rows
here are never updated or deleted by any code in this codebase —
:class:`~developer_suite.models.remote_configuration.RemoteConfiguration`
itself remains the *editable draft* (Phase 5, unchanged), while this
table is the immutable publish history Phase 13 adds on top of it: the
same "draft vs. published, never delete, roll back by creating a new
row" split :class:`~server.models.admin_password_reset.AdminPasswordResetToken`-style
append-only tables already establish elsewhere in this platform.

The actual delivery to the target installation is not a column on
this table — it is a side effect of :meth:`~developer_suite.services.configuration_publish_service.ConfigurationPublishService.publish`
enqueuing exactly one :class:`~developer_suite.models.sync_state.SyncOutboxEntry`
(see :mod:`developer_suite.sync.configuration_sync`), reusing the
exact same outbox/push machinery
:class:`~developer_suite.services.customer_service.CustomerService`
already established in Phase 8. This table's job is only the
Developer-Suite-local record of "what was published, when, by whom,
and why" — the version/checksum/history a Developer Suite user can
browse, compare against, and roll back to.
"""

from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from developer_suite.database.base import DeveloperSuiteBaseModel
from developer_suite.models.customer import Customer
from developer_suite.models.remote_configuration import RemoteConfiguration


class ConfigurationPublication(DeveloperSuiteBaseModel):
    """One immutable, versioned publish of a configuration bundle to one installation.

    Attributes:
        remote_configuration_id: Which editable bundle this publish was
            built from (nullable — see :attr:`checksum`'s docstring for
            why the bundle relationship is informational only, not the
            source of truth for what was actually delivered).
        remote_configuration: The associated
            :class:`~developer_suite.models.remote_configuration.RemoteConfiguration`,
            if it still exists (soft-deletable independently of this
            history).
        customer_id: Which customer this publish's company-information
            fields were snapshotted from.
        customer: The associated :class:`~developer_suite.models.customer.Customer`.
        target_device_public_id: The receiving installation's device
            UUID (a :class:`~server.models.device.SyncDevice.public_id`
            string, of :class:`~server.models.device.DeviceType.ATTENDANCE_CLIENT`)
            — the addressing key
            :mod:`developer_suite.sync.configuration_sync` sends as the
            outbox entry's ``entity_id``, and the value the Attendance
            Client's own puller compares its own device id against
            before applying anything (see
            ``sync/coordinator.py``'s own docstring on the client
            side).
        version: Monotonically increasing per
            ``target_device_public_id`` (``1`` for the first publish to
            a given installation); never reused, including after a
            rollback (a rollback is itself a new, higher version — see
            :meth:`~developer_suite.services.configuration_publish_service.ConfigurationPublishService.rollback`).
        published_by: The admin account's username who triggered this
            publish (see
            :attr:`~developer_suite.admin.auth_client.AdminAccountInfo.username`)
            — a plain string snapshot, not a foreign key, since the
            admin-account system lives entirely server-side (Phase 11)
            and this row must remain readable even if that account is
            later renamed or removed.
        change_summary: A short, human-entered note describing what
            changed in this publish (or, for a rollback, an
            auto-generated note — see :meth:`~developer_suite.services.configuration_publish_service.ConfigurationPublishService.rollback`).
        checksum: The SHA-256 checksum (see
            :func:`~developer_suite.sync.protocol.compute_checksum`) of
            :attr:`payload` — the single source of truth for whether
            two publications are identical, independent of whether
            :attr:`remote_configuration` has since been edited further.
        payload: The complete, self-contained configuration snapshot
            that was actually pushed — company information plus every
            field of all five bundled profiles, exactly as sent over
            the wire (see :func:`~developer_suite.sync.configuration_sync.build_payload`).
            Kept even if :attr:`remote_configuration`/:attr:`customer`
            are later edited or deleted, since a snapshot must survive
            its source's later changes to make rollback meaningful.
    """

    remote_configuration_id: Mapped[int | None] = mapped_column(
        ForeignKey("remote_configurations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    remote_configuration: Mapped["RemoteConfiguration | None"] = relationship("RemoteConfiguration")

    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer: Mapped["Customer"] = relationship("Customer")

    target_device_public_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    published_by: Mapped[str] = mapped_column(String(100), nullable=False)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

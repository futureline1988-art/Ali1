"""simplify to a standalone offline product: drop subscription/sync bookkeeping, add optional update credential

Revision ID: 8f558f3b5277
Revises: 5cc82445bb56
Create Date: 2026-08-05 00:00:00.000000

Drops the three tables and four ``company_settings`` columns that only
ever existed to support the retired central-server subscription/
Company-Code/remote-configuration system, and adds one small optional
table for the decoupled, off-by-default software-update credential
(see ``models.update_credential``'s own docstring). Also adds two
columns to ``devices`` (``device_model``/``firmware_version``,
alongside the already-existing ``serial_number``) populated by the new
diagnostic connection test — see ``devices.device_interface.ConnectionTestResult``.
No existing business data (companies, users, employees, devices,
attendance, ...) is touched or deleted by this migration.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import models.base
import models.encrypted_types


# revision identifiers, used by Alembic.
revision: str = '8f558f3b5277'
down_revision: Union[str, None] = '5cc82445bb56'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('client_subscription_state')
    op.drop_table('client_sync_cursors')
    op.drop_table('client_sync_credential')

    op.drop_column('company_settings', 'remote_config_restart_required')
    op.drop_column('company_settings', 'remote_config_applied_at')
    op.drop_column('company_settings', 'remote_config_checksum')
    op.drop_column('company_settings', 'remote_config_version')

    op.create_table(
        'update_server_credential',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('device_public_id', sa.String(length=36), nullable=False),
        sa.Column('api_key', models.encrypted_types.EncryptedString(length=512), nullable=False),
        sa.Column('server_url', sa.String(length=500), nullable=False),
        sa.Column('registered_at', models.base.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_public_id'),
    )

    op.add_column('devices', sa.Column('device_model', sa.String(length=150), nullable=True))
    op.add_column('devices', sa.Column('firmware_version', sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column('devices', 'firmware_version')
    op.drop_column('devices', 'device_model')

    op.drop_table('update_server_credential')

    op.add_column(
        'company_settings',
        sa.Column('remote_config_version', sa.Integer(), nullable=True),
    )
    op.add_column(
        'company_settings',
        sa.Column('remote_config_checksum', sa.String(length=64), nullable=True),
    )
    op.add_column(
        'company_settings',
        sa.Column('remote_config_applied_at', models.base.UTCDateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'company_settings',
        sa.Column('remote_config_restart_required', sa.Boolean(), server_default=sa.false(), nullable=False),
    )

    op.create_table(
        'client_sync_credential',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('device_public_id', sa.String(length=36), nullable=False),
        sa.Column('api_key', models.encrypted_types.EncryptedString(length=512), nullable=False),
        sa.Column('server_url', sa.String(length=500), nullable=False),
        sa.Column('registered_at', models.base.UTCDateTime(timezone=True), nullable=False),
        sa.Column('bound_company_id', sa.Integer(), nullable=True),
        sa.Column('company_code', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_public_id'),
    )
    op.create_table(
        'client_sync_cursors',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('entity_type', sa.String(length=100), nullable=False),
        sa.Column('last_change_id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('entity_type'),
    )
    op.create_table(
        'client_subscription_state',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('company_name', sa.String(length=200), nullable=True),
        sa.Column('subscription_end_date', sa.Date(), nullable=True),
        sa.Column('max_devices', sa.Integer(), nullable=True),
        sa.Column('days_remaining', sa.Integer(), nullable=True),
        sa.Column('support_phone_primary', sa.String(length=50), nullable=True),
        sa.Column('support_phone_secondary', sa.String(length=50), nullable=True),
        sa.Column('support_whatsapp', sa.String(length=50), nullable=True),
        sa.Column('support_email', sa.String(length=255), nullable=True),
        sa.Column('support_hours', sa.String(length=200), nullable=True),
        sa.Column('support_message', sa.String(length=1000), nullable=True),
        sa.Column('checked_at', models.base.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

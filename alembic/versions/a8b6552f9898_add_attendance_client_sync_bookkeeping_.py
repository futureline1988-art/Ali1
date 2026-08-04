"""add attendance client sync bookkeeping tables

Revision ID: a8b6552f9898
Revises: 0eb6fa1ed18e
Create Date: 2026-08-04 04:50:34.159528

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# UTCDateTime/EncryptedString -- see alembic/versions/f945cdc05edd_baseline_schema.py.
import models.base
import models.encrypted_types


# revision identifiers, used by Alembic.
revision: str = 'a8b6552f9898'
down_revision: Union[str, None] = '0eb6fa1ed18e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('client_sync_credential',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('device_public_id', sa.String(length=36), nullable=False),
    sa.Column('api_key', models.encrypted_types.EncryptedString(length=512), nullable=False),
    sa.Column('server_url', sa.String(length=500), nullable=False),
    sa.Column('registered_at', models.base.UTCDateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_client_sync_credential')),
    sa.UniqueConstraint('device_public_id', name=op.f('uq_client_sync_credential_device_public_id'))
    )
    op.create_table('client_sync_cursors',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('entity_type', sa.String(length=100), nullable=False),
    sa.Column('last_change_id', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_client_sync_cursors')),
    sa.UniqueConstraint('entity_type', name=op.f('uq_client_sync_cursors_entity_type'))
    )


def downgrade() -> None:
    op.drop_table('client_sync_cursors')
    op.drop_table('client_sync_credential')
    # ### end Alembic commands ###

"""add attendance client update-check bookkeeping table

Revision ID: d3c54d8eeb99
Revises: a8b6552f9898
Create Date: 2026-08-04 05:44:56.497489

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# UTCDateTime -- see alembic/versions/f945cdc05edd_baseline_schema.py.
import models.base


# revision identifiers, used by Alembic.
revision: str = 'd3c54d8eeb99'
down_revision: Union[str, None] = 'a8b6552f9898'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('client_update_state',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('update_version_id', sa.Integer(), nullable=False),
    sa.Column('version', sa.String(length=50), nullable=False),
    sa.Column('update_type', sa.String(length=20), nullable=False),
    sa.Column('release_notes', sa.Text(), nullable=True),
    sa.Column('package_id', sa.Integer(), nullable=True),
    sa.Column('package_type', sa.String(length=20), nullable=True),
    sa.Column('checksum_sha256', sa.String(length=64), nullable=True),
    sa.Column('signature_base64', sa.Text(), nullable=True),
    sa.Column('size_bytes', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('downloaded_bytes', sa.Integer(), nullable=False),
    sa.Column('local_file_path', sa.String(length=500), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('postponed_until', models.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('discovered_at', models.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', models.base.UTCDateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_client_update_state'))
    )
    op.create_index(op.f('ix_client_update_state_update_version_id'), 'client_update_state', ['update_version_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_client_update_state_update_version_id'), table_name='client_update_state')
    op.drop_table('client_update_state')
    # ### end Alembic commands ###

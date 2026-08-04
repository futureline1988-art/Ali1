"""add phase 13 remote configuration columns to company_settings

Revision ID: 0eb6fa1ed18e
Revises: f945cdc05edd
Create Date: 2026-08-04 04:40:33.357983

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# UTCDateTime -- see alembic/versions/f945cdc05edd_baseline_schema.py.
import models.base


# revision identifiers, used by Alembic.
revision: str = '0eb6fa1ed18e'
down_revision: Union[str, None] = 'f945cdc05edd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('company_settings', sa.Column('theme_primary_color', sa.String(length=9), nullable=True))
    op.add_column('company_settings', sa.Column('theme_secondary_color', sa.String(length=9), nullable=True))
    op.add_column('company_settings', sa.Column('theme_accent_color', sa.String(length=9), nullable=True))
    op.add_column('company_settings', sa.Column('theme_font_family', sa.String(length=100), nullable=True))
    op.add_column('company_settings', sa.Column('print_settings', sa.JSON(), nullable=True))
    op.add_column('company_settings', sa.Column('attendance_policy_settings', sa.JSON(), nullable=True))
    op.add_column('company_settings', sa.Column('remote_config_version', sa.Integer(), nullable=True))
    op.add_column('company_settings', sa.Column('remote_config_checksum', sa.String(length=64), nullable=True))
    op.add_column('company_settings', sa.Column('remote_config_applied_at', models.base.UTCDateTime(timezone=True), nullable=True))
    # NOT NULL with a server_default so existing rows (predating Phase 13)
    # backfill to False instead of failing the ALTER TABLE.
    op.add_column(
        'company_settings',
        sa.Column('remote_config_restart_required', sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('company_settings', 'remote_config_restart_required')
    op.drop_column('company_settings', 'remote_config_applied_at')
    op.drop_column('company_settings', 'remote_config_checksum')
    op.drop_column('company_settings', 'remote_config_version')
    op.drop_column('company_settings', 'attendance_policy_settings')
    op.drop_column('company_settings', 'print_settings')
    op.drop_column('company_settings', 'theme_font_family')
    op.drop_column('company_settings', 'theme_accent_color')
    op.drop_column('company_settings', 'theme_secondary_color')
    op.drop_column('company_settings', 'theme_primary_color')
    # ### end Alembic commands ###

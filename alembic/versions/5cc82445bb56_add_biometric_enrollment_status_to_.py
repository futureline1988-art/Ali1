"""add biometric enrollment status to employees

Revision ID: 5cc82445bb56
Revises: 2115fcaea0de
Create Date: 2026-08-05 10:25:57.528000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# UTCDateTime -- see alembic/versions/f945cdc05edd_baseline_schema.py.
import models.base


# revision identifiers, used by Alembic.
revision: str = '5cc82445bb56'
down_revision: Union[str, None] = '2115fcaea0de'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOT NULL with a server_default so existing rows (predating this
    # feature) backfill to False/0 instead of failing the ALTER TABLE.
    op.add_column(
        'employees',
        sa.Column('face_enrolled', sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        'employees',
        sa.Column('face_enrolled_device_id', sa.Integer(), nullable=True),
    )
    # SQLite cannot ALTER a table to add a constraint directly -- batch
    # mode is Alembic's documented copy-and-move strategy for this.
    with op.batch_alter_table('employees') as batch_op:
        batch_op.create_foreign_key(
            'fk_employees_face_enrolled_device_id_devices',
            'devices',
            ['face_enrolled_device_id'],
            ['id'],
            ondelete='SET NULL',
        )
    op.add_column(
        'employees',
        sa.Column('face_enrolled_at', models.base.UTCDateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'employees',
        sa.Column('fingerprint_count', sa.Integer(), server_default='0', nullable=False),
    )
    op.add_column(
        'employees',
        sa.Column('card_assigned', sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        'employees',
        sa.Column('biometric_last_synced_at', models.base.UTCDateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'employees',
        sa.Column('biometric_last_verification_result', sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('employees', 'biometric_last_verification_result')
    op.drop_column('employees', 'biometric_last_synced_at')
    op.drop_column('employees', 'card_assigned')
    op.drop_column('employees', 'fingerprint_count')
    op.drop_column('employees', 'face_enrolled_at')
    with op.batch_alter_table('employees') as batch_op:
        batch_op.drop_constraint(
            'fk_employees_face_enrolled_device_id_devices', type_='foreignkey'
        )
    op.drop_column('employees', 'face_enrolled_device_id')
    op.drop_column('employees', 'face_enrolled')

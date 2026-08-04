"""drop unused legacy per-company licenses table

The ``licenses`` table (:class:`~models.license.License`, a
per-company SaaS subscription-limits record with a ``String(255)``
``license_key`` column) predates the shared :mod:`licensing` package's
machine-locked, Ed25519-signed activation model and was never wired
into any service, controller, or UI in this codebase -- confirmed
unused by grepping the whole tree for its model, repository, and the
``max_users``/``max_devices``/``max_branches``/``enabled_features``
fields it defined. Removed as part of unifying the licensing system on
a single format (see :mod:`licensing.license_key`'s ``AMS1.<payload>.
<signature>`` keys, the only license format now used anywhere in this
codebase).

Revision ID: 551b9791696c
Revises: d3c54d8eeb99
Create Date: 2026-08-04 15:50:38.479530

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# UTCDateTime -- see alembic/versions/f945cdc05edd_baseline_schema.py.
import models.base


# revision identifiers, used by Alembic.
revision: str = '551b9791696c'
down_revision: Union[str, None] = 'd3c54d8eeb99'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(op.f('ix_licenses_public_id'), table_name='licenses')
    op.drop_index(op.f('ix_licenses_license_key'), table_name='licenses')
    op.drop_index(op.f('ix_licenses_is_deleted'), table_name='licenses')
    op.drop_index(op.f('ix_licenses_company_id'), table_name='licenses')
    op.drop_table('licenses')


def downgrade() -> None:
    op.create_table('licenses',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('public_id', sa.Uuid(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('license_key', sa.String(length=255), nullable=False),
    sa.Column('issued_at', sa.Date(), nullable=False),
    sa.Column('expires_at', sa.Date(), nullable=True),
    sa.Column('max_users', sa.Integer(), nullable=True),
    sa.Column('max_devices', sa.Integer(), nullable=True),
    sa.Column('max_branches', sa.Integer(), nullable=True),
    sa.Column('enabled_features', sa.JSON(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', models.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('updated_at', models.base.UTCDateTime(timezone=True), nullable=False),
    sa.Column('is_deleted', sa.Boolean(), nullable=False),
    sa.Column('deleted_at', models.base.UTCDateTime(timezone=True), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('updated_by_id', sa.Integer(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_licenses_company_id_companies')),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_licenses_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name=op.f('fk_licenses_updated_by_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_licenses'))
    )
    op.create_index(op.f('ix_licenses_company_id'), 'licenses', ['company_id'], unique=False)
    op.create_index(op.f('ix_licenses_is_deleted'), 'licenses', ['is_deleted'], unique=False)
    op.create_index(op.f('ix_licenses_license_key'), 'licenses', ['license_key'], unique=True)
    op.create_index(op.f('ix_licenses_public_id'), 'licenses', ['public_id'], unique=True)

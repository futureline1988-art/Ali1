"""add company code to client sync credential

Revision ID: e65ed4cf81cf
Revises: 23757b4b3dac
Create Date: 2026-08-05 06:34:00.389999

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e65ed4cf81cf'
down_revision: Union[str, None] = '23757b4b3dac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("client_sync_credential", sa.Column("company_code", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("client_sync_credential", "company_code")

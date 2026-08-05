"""add device company binding to client sync credential

Revision ID: 23757b4b3dac
Revises: 80c7558c5e4d
Create Date: 2026-08-05 05:42:23.063270

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '23757b4b3dac'
down_revision: Union[str, None] = '80c7558c5e4d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("client_sync_credential", sa.Column("bound_company_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("client_sync_credential", "bound_company_id")

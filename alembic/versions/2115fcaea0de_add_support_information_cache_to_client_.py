"""add support information cache to client subscription state

Revision ID: 2115fcaea0de
Revises: e65ed4cf81cf
Create Date: 2026-08-05 07:37:12.334823

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2115fcaea0de'
down_revision: Union[str, None] = 'e65ed4cf81cf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SUPPORT_COLUMNS = (
    ("support_phone_primary", sa.String(length=50)),
    ("support_phone_secondary", sa.String(length=50)),
    ("support_whatsapp", sa.String(length=50)),
    ("support_email", sa.String(length=255)),
    ("support_hours", sa.String(length=200)),
    ("support_message", sa.String(length=1000)),
)


def upgrade() -> None:
    for name, column_type in _SUPPORT_COLUMNS:
        op.add_column("client_subscription_state", sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_SUPPORT_COLUMNS):
        op.drop_column("client_subscription_state", name)

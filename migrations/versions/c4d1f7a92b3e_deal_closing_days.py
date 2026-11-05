"""deal closing days

Revision ID: c4d1f7a92b3e
Revises: bfaa2ee4016e
Create Date: 2026-11-05 13:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d1f7a92b3e'
down_revision: Union[str, None] = 'bfaa2ee4016e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('wholesale_deals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('closing_days', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('wholesale_deals', schema=None) as batch_op:
        batch_op.drop_column('closing_days')

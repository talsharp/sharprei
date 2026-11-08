"""facebook leads outside the target area

Revision ID: d9a4f1c7e2b5
Revises: c6e1a3b8d5f2
Create Date: 2026-11-08 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9a4f1c7e2b5'
down_revision: Union[str, None] = 'c6e1a3b8d5f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('finance_months', schema=None) as batch_op:
        batch_op.add_column(sa.Column('facebook_outside_leads', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('finance_months', schema=None) as batch_op:
        batch_op.drop_column('facebook_outside_leads')

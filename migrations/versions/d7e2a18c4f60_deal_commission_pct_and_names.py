"""deal commission percent and names

Revision ID: d7e2a18c4f60
Revises: c4d1f7a92b3e
Create Date: 2026-11-05 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7e2a18c4f60'
down_revision: Union[str, None] = 'c4d1f7a92b3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('wholesale_deals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('realtor_pct', sa.Numeric(precision=6, scale=2), nullable=True))
        batch_op.add_column(sa.Column('acquisition_pct', sa.Numeric(precision=6, scale=2), nullable=True))
        batch_op.add_column(sa.Column('realtor_name', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('acquisition_name', sa.String(length=120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('wholesale_deals', schema=None) as batch_op:
        batch_op.drop_column('acquisition_name')
        batch_op.drop_column('realtor_name')
        batch_op.drop_column('acquisition_pct')
        batch_op.drop_column('realtor_pct')

"""deal showings, buyer offer amount, new buyer statuses

Revision ID: c6e1a3b8d5f2
Revises: b2f7d9e4a6c1
Create Date: 2026-11-08 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c6e1a3b8d5f2'
down_revision: Union[str, None] = 'b2f7d9e4a6c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'deal_showings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('deal_id', sa.Integer(), nullable=False),
        sa.Column('at', sa.DateTime(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['deal_id'], ['wholesale_deals.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_deal_showings_deal_id', 'deal_showings', ['deal_id'])
    with op.batch_alter_table('deal_buyers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('offer_amount', sa.Numeric(precision=12, scale=2), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('deal_buyers', schema=None) as batch_op:
        batch_op.drop_column('offer_amount')
    op.drop_table('deal_showings')

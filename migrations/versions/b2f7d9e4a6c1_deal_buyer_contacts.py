"""deal buyer contacted yes/no

Revision ID: b2f7d9e4a6c1
Revises: a8c4e61f09b2
Create Date: 2026-11-08 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2f7d9e4a6c1'
down_revision: Union[str, None] = 'a8c4e61f09b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'deal_buyer_contacts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('deal_id', sa.Integer(), nullable=False),
        sa.Column('buyer_id', sa.Integer(), nullable=False),
        sa.Column('contacted', sa.Boolean(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['deal_id'], ['wholesale_deals.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['buyer_id'], ['buyers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('deal_id', 'buyer_id'),
    )
    op.create_index('ix_deal_buyer_contacts_deal_id', 'deal_buyer_contacts', ['deal_id'])
    op.create_index('ix_deal_buyer_contacts_buyer_id', 'deal_buyer_contacts', ['buyer_id'])


def downgrade() -> None:
    op.drop_table('deal_buyer_contacts')

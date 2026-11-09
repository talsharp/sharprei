"""deal views on other platforms (InvestorBase etc.)

Revision ID: e8b3c7a1f4d6
Revises: d9a4f1c7e2b5
Create Date: 2026-11-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8b3c7a1f4d6'
down_revision: Union[str, None] = 'd9a4f1c7e2b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'deal_platform_views',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('deal_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('views', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['deal_id'], ['wholesale_deals.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_deal_platform_views_deal_id', 'deal_platform_views', ['deal_id'])


def downgrade() -> None:
    op.drop_table('deal_platform_views')

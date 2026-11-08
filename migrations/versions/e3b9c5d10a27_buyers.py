"""buyers

Revision ID: e3b9c5d10a27
Revises: d7e2a18c4f60
Create Date: 2026-11-08 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3b9c5d10a27'
down_revision: Union[str, None] = 'd7e2a18c4f60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'buyers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('email', sa.String(length=200), nullable=True),
        sa.Column('phone', sa.String(length=40), nullable=True),
        sa.Column('channels', sa.String(length=40), nullable=False),
        sa.Column('zip_codes', sa.Text(), nullable=True),
        sa.Column('neighborhoods', sa.Text(), nullable=True),
        sa.Column('areas', sa.Text(), nullable=True),
        sa.Column('max_price', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('property_types', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('original_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('buyers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_buyers_name'), ['name'], unique=False)
        batch_op.create_index(batch_op.f('ix_buyers_email'), ['email'], unique=False)
    with op.batch_alter_table('deal_buyers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('buyer_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_deal_buyers_buyer_id'), ['buyer_id'], unique=False)
        batch_op.create_foreign_key('fk_deal_buyers_buyer_id', 'buyers', ['buyer_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    with op.batch_alter_table('deal_buyers', schema=None) as batch_op:
        batch_op.drop_constraint('fk_deal_buyers_buyer_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_deal_buyers_buyer_id'))
        batch_op.drop_column('buyer_id')
    with op.batch_alter_table('buyers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_buyers_email'))
        batch_op.drop_index(batch_op.f('ix_buyers_name'))
    op.drop_table('buyers')

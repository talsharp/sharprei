"""GHL email history, personal link buyer, deal neighborhood

Revision ID: a8c4e61f09b2
Revises: f5a0d2e7c381
Create Date: 2026-11-08 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8c4e61f09b2'
down_revision: Union[str, None] = 'f5a0d2e7c381'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ghl_emails',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('deal_id', sa.Integer(), nullable=True),
        sa.Column('address', sa.String(length=255), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=False),
        sa.Column('sent_on', sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(['deal_id'], ['wholesale_deals.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ghl_emails_deal_id', 'ghl_emails', ['deal_id'])
    op.create_table(
        'ghl_email_recipients',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email_id', sa.Integer(), nullable=False),
        sa.Column('buyer_id', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(length=120), nullable=True),
        sa.Column('address', sa.String(length=200), nullable=False),
        sa.Column('opened', sa.Boolean(), nullable=False),
        sa.Column('clicked', sa.Boolean(), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=True),
        sa.ForeignKeyConstraint(['email_id'], ['ghl_emails.id']),
        sa.ForeignKeyConstraint(['buyer_id'], ['buyers.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ghl_email_recipients_email_id', 'ghl_email_recipients', ['email_id'])
    op.create_index('ix_ghl_email_recipients_buyer_id', 'ghl_email_recipients', ['buyer_id'])
    op.create_index('ix_ghl_email_recipients_address', 'ghl_email_recipients', ['address'])
    with op.batch_alter_table('deal_page_links', schema=None) as batch_op:
        batch_op.add_column(sa.Column('buyer_id', sa.Integer(), nullable=True))
        batch_op.create_index('ix_deal_page_links_buyer_id', ['buyer_id'], unique=False)
        batch_op.create_foreign_key('fk_deal_page_links_buyer_id', 'buyers', ['buyer_id'], ['id'], ondelete='SET NULL')
    with op.batch_alter_table('wholesale_deals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('neighborhood', sa.String(length=120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('wholesale_deals', schema=None) as batch_op:
        batch_op.drop_column('neighborhood')
    with op.batch_alter_table('deal_page_links', schema=None) as batch_op:
        batch_op.drop_constraint('fk_deal_page_links_buyer_id', type_='foreignkey')
        batch_op.drop_index('ix_deal_page_links_buyer_id')
        batch_op.drop_column('buyer_id')
    op.drop_table('ghl_email_recipients')
    op.drop_table('ghl_emails')

"""portfolio property invoices & expenses

Revision ID: f2c8d4b6a1e9
Revises: e8b3c7a1f4d6
Create Date: 2026-11-11 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2c8d4b6a1e9'
down_revision: Union[str, None] = 'e8b3c7a1f4d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'property_invoices',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('property_id', sa.Integer(), nullable=False),
        sa.Column('invoice_date', sa.Date(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('original_name', sa.String(length=255), nullable=True),
        sa.Column('stored_name', sa.String(length=255), nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_property_invoices_property_id', 'property_invoices', ['property_id'])


def downgrade() -> None:
    op.drop_table('property_invoices')

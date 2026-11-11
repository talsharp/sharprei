"""service providers (Tools > Service Providers)

Revision ID: a3d7e9f1b5c2
Revises: f2c8d4b6a1e9
Create Date: 2026-11-11 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3d7e9f1b5c2'
down_revision: Union[str, None] = 'f2c8d4b6a1e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    types = op.create_table(
        'provider_types',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    # The types Tal started with (2026-11-05).
    op.bulk_insert(types, [{"name": n, "position": i} for i, n in enumerate(["Contractor", "Boots", "Cleaning", "Cleanout/haul", "Plumbing"])])
    op.create_table(
        'service_providers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('type_id', sa.Integer(), nullable=True),
        sa.Column('section', sa.String(length=20), nullable=False),
        sa.Column('phone', sa.String(length=40), nullable=True),
        sa.Column('email', sa.String(length=200), nullable=True),
        sa.Column('facebook', sa.String(length=255), nullable=True),
        sa.Column('recommended_by', sa.String(length=120), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['type_id'], ['provider_types.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'provider_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('provider_id', sa.Integer(), nullable=False),
        sa.Column('worked_on', sa.Date(), nullable=False),
        sa.Column('rating', sa.String(length=10), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['provider_id'], ['service_providers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_provider_jobs_provider_id', 'provider_jobs', ['provider_id'])


def downgrade() -> None:
    op.drop_table('provider_jobs')
    op.drop_table('service_providers')
    op.drop_table('provider_types')

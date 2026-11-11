"""service provider jobs: the work and what they charged

Revision ID: c7f3a9d2e6b8
Revises: b5e2f8a3c9d4
Create Date: 2026-11-11 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7f3a9d2e6b8'
down_revision: Union[str, None] = 'b5e2f8a3c9d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('provider_jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('work', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('provider_jobs', schema=None) as batch_op:
        batch_op.drop_column('amount')
        batch_op.drop_column('work')

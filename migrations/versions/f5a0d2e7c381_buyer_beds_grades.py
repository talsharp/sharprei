"""buyer bedrooms and grades, deal bedrooms and neighborhood grade

Revision ID: f5a0d2e7c381
Revises: e3b9c5d10a27
Create Date: 2026-11-08 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f5a0d2e7c381'
down_revision: Union[str, None] = 'e3b9c5d10a27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('buyers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('min_beds', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('max_beds', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('grades', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('exact_area_only', sa.Boolean(), server_default='0', nullable=False))
    with op.batch_alter_table('wholesale_deals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('bedrooms', sa.Numeric(precision=4, scale=1), nullable=True))
        batch_op.add_column(sa.Column('neighborhood_grade', sa.String(length=2), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('wholesale_deals', schema=None) as batch_op:
        batch_op.drop_column('neighborhood_grade')
        batch_op.drop_column('bedrooms')
    with op.batch_alter_table('buyers', schema=None) as batch_op:
        batch_op.drop_column('exact_area_only')
        batch_op.drop_column('grades')
        batch_op.drop_column('max_beds')
        batch_op.drop_column('min_beds')

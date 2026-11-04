"""deal page views: on_screen and user_agent

Revision ID: bfaa2ee4016e
Revises: 1459ba4d041c
Create Date: 2026-11-04 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bfaa2ee4016e'
down_revision: Union[str, None] = '1459ba4d041c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('deal_page_views', schema=None) as batch_op:
        batch_op.add_column(sa.Column('on_screen', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('user_agent', sa.String(length=300), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('deal_page_views', schema=None) as batch_op:
        batch_op.drop_column('user_agent')
        batch_op.drop_column('on_screen')

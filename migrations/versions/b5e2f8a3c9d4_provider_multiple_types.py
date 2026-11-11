"""service providers: several types each

Revision ID: b5e2f8a3c9d4
Revises: a3d7e9f1b5c2
Create Date: 2026-11-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5e2f8a3c9d4'
down_revision: Union[str, None] = 'a3d7e9f1b5c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('service_providers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('types', sa.Text(), nullable=True))
    # Each provider's one type so far becomes their list.
    op.execute("UPDATE service_providers SET types = (SELECT name FROM provider_types WHERE provider_types.id = service_providers.type_id) WHERE type_id IS NOT NULL")


def downgrade() -> None:
    with op.batch_alter_table('service_providers', schema=None) as batch_op:
        batch_op.drop_column('types')

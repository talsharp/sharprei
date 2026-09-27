"""baseline - schema as of 2026-09-27

Revision ID: 01a81e5ba8d0
Revises: 
Create Date: 2026-09-27 12:35:01.839233

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '01a81e5ba8d0'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

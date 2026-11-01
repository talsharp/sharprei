"""deal tasks join the tasks board, soft delete

Revision ID: 0153161ca21c
Revises: 2fbdf447c4f1
Create Date: 2026-11-01 09:52:33.404093

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0153161ca21c'
down_revision: Union[str, None] = '2fbdf447c4f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("deal_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("due_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("deal_position", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("board_hidden", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
        batch_op.create_index(batch_op.f("ix_tasks_deal_id"), ["deal_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_tasks_due_date"), ["due_date"], unique=False)
        batch_op.create_index(batch_op.f("ix_tasks_deleted_at"), ["deleted_at"], unique=False)
        batch_op.create_foreign_key("fk_tasks_deal_id", "wholesale_deals", ["deal_id"], ["id"])

    # Copy the deal tasks over. Their old statuses become the board's ones.
    conn = op.get_bind()
    statuses = conn.execute(sa.text("SELECT id, name, is_done FROM task_statuses ORDER BY position, id")).fetchall()
    by_name = {name.strip().lower(): sid for sid, name, _ in statuses}
    open_ids = [sid for sid, _, done in statuses if not done]
    done_ids = [sid for sid, _, done in statuses if done]
    first_open = open_ids[0] if open_ids else None
    mapping = {
        "todo": by_name.get("not started", first_open),
        "in_progress": by_name.get("working on it", first_open),
        "waiting": by_name.get("stuck", first_open),
        "done": done_ids[0] if done_ids else None,
    }
    last = conn.execute(sa.text("SELECT COALESCE(MAX(position), 0) FROM tasks")).scalar() or 0
    rows = conn.execute(sa.text(
        "SELECT id, deal_id, title, details, due_date, status, created_at FROM deal_tasks "
        "ORDER BY deal_id, status = 'done', due_date IS NULL, due_date, id")).fetchall()
    place = {}
    for i, (_, deal_id, title, details, due_date, status, created_at) in enumerate(rows, start=1):
        place[deal_id] = place.get(deal_id, -1) + 1
        conn.execute(sa.text(
            "INSERT INTO tasks (title, description, day, position, status_id, deal_id, due_date, deal_position, board_hidden, created_at) "
            "VALUES (:title, :description, 'later', :position, :status_id, :deal_id, :due_date, :deal_position, 0, :created_at)"),
            {"title": title, "description": details, "position": last + i, "status_id": mapping.get(status, first_open),
             "deal_id": deal_id, "due_date": due_date, "deal_position": place[deal_id], "created_at": created_at})

    with op.batch_alter_table("deal_tasks", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_deal_tasks_deal_id"))
        batch_op.drop_index(batch_op.f("ix_deal_tasks_due_date"))
    op.drop_table("deal_tasks")


def downgrade() -> None:
    op.create_table(
        "deal_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["deal_id"], ["wholesale_deals.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("deal_tasks", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_deal_tasks_deal_id"), ["deal_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_deal_tasks_due_date"), ["due_date"], unique=False)
    op.execute("DELETE FROM tasks WHERE deal_id IS NOT NULL")
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_constraint("fk_tasks_deal_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_tasks_deleted_at"))
        batch_op.drop_index(batch_op.f("ix_tasks_due_date"))
        batch_op.drop_index(batch_op.f("ix_tasks_deal_id"))
        batch_op.drop_column("deleted_at")
        batch_op.drop_column("board_hidden")
        batch_op.drop_column("deal_position")
        batch_op.drop_column("due_date")
        batch_op.drop_column("deal_id")

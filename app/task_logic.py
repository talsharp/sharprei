"""
Rules shared by the Tasks board and the deal pages (Tal's spec, 2026-11-01).

Deal tasks live on the board too:
- their day on the board comes from their date within this Sun–Sat week;
  later dates and no date = Later; late (not done) = today's column.
- moving one to another day on the board sets its date to that day this week;
  moving it to Later removes the date.
In the deal's own list Tal sets the order by dragging. A new task, or one whose
date changes, is placed by its date among the others; done tasks stay put.

Deleted tasks stay 3 days in "Recently deleted", then they're gone.
"""

from datetime import date, datetime, timedelta
from typing import List, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.finance import local_today
from app.models import Task, TaskStatus

WEEK_KEYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
KEEP_DELETED = timedelta(days=3)


def day_key(d: date) -> str:
    return WEEK_KEYS[(d.weekday() + 1) % 7]


def week_start(today: date) -> date:
    """This week's Sunday."""
    return today - timedelta(days=(today.weekday() + 1) % 7)


def date_for_day(day: str, today: date) -> Optional[date]:
    """The date of that weekday this week; None for Later."""
    if day not in WEEK_KEYS:
        return None
    return week_start(today) + timedelta(days=WEEK_KEYS.index(day))


def days_late(task: Task, today: date) -> int:
    if task.deal_id and task.due_date and not task.is_done and task.due_date < today:
        return (today - task.due_date).days
    return 0


def board_day(task: Task, today: date) -> Optional[str]:
    """Which group the task shows in on the board, or None = not on the board."""
    if not task.deal_id:
        return task.day if task.day in WEEK_KEYS + ["later"] else "later"
    if task.board_hidden:
        return None
    if task.due_date is None:
        return "later"
    if days_late(task, today):
        return day_key(today)
    start = week_start(today)
    if task.due_date < start:
        return None  # done in an earlier week
    if task.due_date > start + timedelta(days=6):
        return "later"
    return day_key(task.due_date)


def live_tasks(db: Session):
    return db.query(Task).filter(Task.deleted_at.is_(None))


def default_status(db: Session) -> Optional[TaskStatus]:
    first = db.query(TaskStatus).order_by(TaskStatus.position, TaskStatus.id).first()
    return first if first and not first.is_done else None


def done_status(db: Session) -> Optional[TaskStatus]:
    return db.query(TaskStatus).filter(TaskStatus.is_done.is_(True)).order_by(TaskStatus.position, TaskStatus.id).first()


def deal_tasks(db: Session, deal_id: int) -> List[Task]:
    db.flush()
    return live_tasks(db).filter(Task.deal_id == deal_id).order_by(Task.deal_position, Task.id).all()


def _renumber(tasks: List[Task]) -> None:
    for i, t in enumerate(tasks):
        t.deal_position = i


def place_by_date(db: Session, task: Task) -> None:
    """Puts the task in its date's spot in the deal's list: before the first open
    task that has a later date (or none). No date = at the bottom."""
    others = [t for t in deal_tasks(db, task.deal_id) if t.id != task.id]
    at = len(others)
    if task.due_date:
        for i, t in enumerate(others):
            if not t.is_done and (t.due_date is None or t.due_date > task.due_date):
                at = i
                break
    others.insert(at, task)
    _renumber(others)


def sort_by_date(db: Session, deal_id: int) -> None:
    tasks = deal_tasks(db, deal_id)
    tasks.sort(key=lambda t: (t.due_date is None, t.due_date or date.min, t.id))
    _renumber(tasks)


def set_deal_order(db: Session, deal_id: int, ids: List[int]) -> None:
    tasks = {t.id: t for t in deal_tasks(db, deal_id)}
    ordered = [tasks.pop(i) for i in ids if i in tasks] + sorted(tasks.values(), key=lambda t: t.deal_position)
    _renumber(ordered)


def set_due_date(db: Session, task: Task, due: Optional[date]) -> bool:
    """Changes a deal task's date; it jumps to its new spot in the deal list."""
    if task.due_date == due:
        return False
    task.due_date = due
    if task.deal_id:
        place_by_date(db, task)
    return True


def next_board_position(db: Session) -> int:
    return (db.query(func.max(Task.position)).scalar() or 0) + 1


def soft_delete(task: Task) -> None:
    task.deleted_at = datetime.utcnow()


def purge_deleted(db: Session) -> None:
    db.query(Task).filter(Task.deleted_at.isnot(None), Task.deleted_at < datetime.utcnow() - KEEP_DELETED).delete(
        synchronize_session=False)


def open_filter():
    """SQL filter: status not marked as done (or no status)."""
    return or_(Task.status_id.is_(None), Task.status_id.in_(
        TaskStatus.__table__.select().with_only_columns(TaskStatus.id).where(TaskStatus.is_done.is_(False))))


def due_tasks(db: Session) -> List[Task]:
    """Deal tasks due today or late and not done - the reminders."""
    return (
        live_tasks(db)
        .filter(Task.deal_id.isnot(None), Task.due_date.isnot(None), Task.due_date <= local_today(), open_filter())
        .order_by(Task.due_date, Task.id)
        .all()
    )

"""Tools › Tasks: a Monday-style board with weekday groups, statuses and labels.

Groups are fixed weekdays (Sunday first) plus "Later" - names, not dates, so a
task stays on its day until it's dragged elsewhere. Deal tasks show here too,
on their date's day this week (see app/task_logic.py). Deleting a task, or
"Clear done", keeps it 3 days in "Recently deleted".
"""

from collections import OrderedDict
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.finance import local_today
from app.models import Task, TaskLabel, TaskStatus, WholesaleDeal
from app.task_logic import (
    WEEK_KEYS, board_day, date_for_day, day_key, live_tasks, place_by_date, purge_deleted, set_due_date,
    soft_delete,
)

DAYS = OrderedDict(
    [
        ("sun", "Sunday"),
        ("mon", "Monday"),
        ("tue", "Tuesday"),
        ("wed", "Wednesday"),
        ("thu", "Thursday"),
        ("fri", "Friday"),
        ("sat", "Saturday"),
        ("later", "Later"),
    ]
)

# The groups shown: the days, then Upcoming (deal tasks dated after this week -
# they move into their day by themselves; nothing is dragged into it), then Later.
GROUPS = OrderedDict(list(DAYS.items())[:-1] + [("upcoming", "Upcoming"), ("later", "Later")])

OPTION_MODELS = {"status": TaskStatus, "label": TaskLabel}


def _int_or_none(value):
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _color(value: str, fallback: str) -> str:
    value = (value or "").strip()
    return value if len(value) == 7 and value.startswith("#") else fallback


def _street(deal) -> str:
    return (deal.address or "").split(",")[0] if deal else ""


def _ago(when: datetime) -> str:
    hours = int((datetime.utcnow() - when).total_seconds() // 3600)
    if hours < 1:
        return "just now"
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    return f"{hours // 24} day{'s' if hours // 24 != 1 else ''} ago"


def _move_to_day(db: Session, task: Task, day: str, today) -> None:
    """A deal task's day is its date: moving it changes the date (Later = no date)."""
    if task.deal_id:
        if board_day(task, today) != day:
            set_due_date(db, task, date_for_day(day, today))
        task.board_hidden = False
    else:
        task.day = day


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/tools/tasks")
    templates.env.globals["task_street"] = _street

    def _options(db: Session):
        statuses = db.query(TaskStatus).order_by(TaskStatus.position, TaskStatus.id).all()
        labels = db.query(TaskLabel).order_by(TaskLabel.position, TaskLabel.id).all()
        return statuses, labels

    def _row(request: Request, db: Session, task: Task):
        statuses, labels = _options(db)
        return templates.TemplateResponse(
            "_task_row.html", {"request": request, "t": task, "statuses": statuses, "labels": labels, "days": DAYS}
        )

    @router.get("")
    def tasks_page(request: Request, db: Session = Depends(get_db)):
        purge_deleted(db)
        db.commit()
        today = local_today()
        statuses, labels = _options(db)
        groups = OrderedDict((key, []) for key in GROUPS)
        on_board = []
        for t in live_tasks(db).order_by(Task.position, Task.id).all():
            day = board_day(t, today)
            if day:
                groups[day].append(t)
                on_board.append(t)
        groups["upcoming"].sort(key=lambda t: (t.due_date, t.position, t.id))
        deleted = db.query(Task).filter(Task.deleted_at.isnot(None)).order_by(Task.deleted_at.desc()).all()
        deals = db.query(WholesaleDeal).filter(WholesaleDeal.status == "under_contract").order_by(WholesaleDeal.address).all()
        return templates.TemplateResponse(
            "tools_tasks.html",
            {
                "request": request,
                "active": "tasks",
                "days": DAYS,
                "group_names": GROUPS,
                "groups": groups,
                "statuses": statuses,
                "labels": labels,
                "done_count": sum(1 for t in on_board if t.is_done),
                "deleted": [(t, _ago(t.deleted_at)) for t in deleted],
                "today_key": day_key(today),
                "options_json": {
                    "status": [{"id": o.id, "name": o.name, "color": o.color, "done": o.is_done} for o in statuses],
                    "label": [{"id": o.id, "name": o.name, "color": o.color} for o in labels],
                    "deal": [{"id": d.id, "name": _street(d)} for d in deals],
                    "today": today.isoformat(),
                    "week": {k: date_for_day(k, today).isoformat() for k in WEEK_KEYS},
                },
            },
        )

    @router.post("/new")
    def task_new(request: Request, title: str = Form(""), day: str = Form("later"), db: Session = Depends(get_db)):
        title = title.strip()[:255]
        if not title:
            return Response(status_code=204)
        day = day if day in DAYS else "later"
        last = db.query(func.max(Task.position)).filter(Task.day == day).scalar()
        default_status = db.query(TaskStatus).order_by(TaskStatus.position, TaskStatus.id).first()
        task = Task(title=title, day=day, position=(last or 0) + 1,
                    status_id=default_status.id if default_status and not default_status.is_done else None)
        db.add(task)
        db.commit()
        return _row(request, db, task)

    @router.post("/clear-done")
    def tasks_clear_done(db: Session = Depends(get_db)):
        """Done tasks leave the board. Board tasks go to Recently deleted; deal
        tasks just leave the board - the deal keeps them."""
        today = local_today()
        cleared = []
        for task in live_tasks(db).all():
            if task.is_done and board_day(task, today):
                if task.deal_id:
                    task.board_hidden = True
                else:
                    soft_delete(task)
                cleared.append(task.id)
        db.commit()
        return {"ids": cleared}

    @router.post("/restore")
    async def tasks_restore_many(request: Request, db: Session = Depends(get_db)):
        """Undo for "Clear done". Body: {"ids": [...]}."""
        data = await request.json()
        ids = [i for i in (_int_or_none(x) for x in data.get("ids", [])) if i]
        for task in db.query(Task).filter(Task.id.in_(ids)).all():
            task.deleted_at = None
            task.board_hidden = False
        db.commit()
        return Response(status_code=204)

    @router.post("/move")
    async def tasks_move(request: Request, db: Session = Depends(get_db)):
        """Body: {"day": "mon", "ids": [3, 1, 2]} - the new order of that group."""
        data = await request.json()
        day = data.get("day")
        if day not in DAYS:
            return Response(status_code=400)
        today = local_today()
        ids = [i for i in (_int_or_none(x) for x in data.get("ids", [])) if i]
        tasks = {t.id: t for t in live_tasks(db).filter(Task.id.in_(ids)).all()}
        for position, task_id in enumerate(ids):
            task = tasks.get(task_id)
            if task:
                _move_to_day(db, task, day, today)
                task.position = position
        db.commit()
        return Response(status_code=204)

    @router.post("/{task_id}")
    async def task_update(task_id: int, request: Request, db: Session = Depends(get_db)):
        """Saves whichever fields were sent (title, description, status_id,
        label_id, day, deal_id). Returns the task's deal and date afterwards."""
        task = db.get(Task, task_id)
        if not task:
            return Response(status_code=404)
        form = await request.form()
        today = local_today()
        if "title" in form and form["title"].strip():
            task.title = form["title"].strip()[:255]
        if "description" in form:
            task.description = form["description"].strip() or None
        if "status_id" in form:
            task.status_id = _int_or_none(form["status_id"])
            db.flush()
            db.refresh(task)
            if not task.is_done:
                task.board_hidden = False
        if "label_id" in form:
            task.label_id = _int_or_none(form["label_id"])
        if "deal_id" in form:
            deal_id = _int_or_none(form["deal_id"])
            deal = db.get(WholesaleDeal, deal_id) if deal_id else None
            if deal and deal.id != task.deal_id:
                if not task.deal_id:  # a board task joins a deal: its day becomes its date
                    task.due_date = date_for_day(task.day, today)
                task.deal_id = deal.id
                place_by_date(db, task)
            elif not deal and task.deal_id:  # back to a plain board task, on the same day
                day = board_day(task, today)
                task.day = day if day in WEEK_KEYS else "later"
                task.deal_id = None
                task.due_date = None
                task.board_hidden = False
        if "day" in form and form["day"] in DAYS and form["day"] != board_day(task, today):
            _move_to_day(db, task, form["day"], today)
            last = db.query(func.max(Task.position)).scalar()
            task.position = (last or 0) + 1
        db.commit()
        db.refresh(task)
        return {"deal_id": task.deal_id, "deal": _street(task.deal),
                "due": task.due_date.isoformat() if task.due_date else "", "day": board_day(task, today) or ""}

    @router.post("/{task_id}/delete")
    def task_delete(task_id: int, db: Session = Depends(get_db)):
        task = db.get(Task, task_id)
        if task:
            soft_delete(task)
            db.commit()
        return Response(status_code=204)

    @router.post("/{task_id}/restore")
    def task_restore(task_id: int, db: Session = Depends(get_db)):
        task = db.get(Task, task_id)
        if task:
            task.deleted_at = None
            db.commit()
        return Response(status_code=204)

    # ---------- Statuses and labels ----------

    @router.post("/options/{kind}/new")
    def option_new(kind: str, name: str = Form(""), color: str = Form(""), db: Session = Depends(get_db)):
        model = OPTION_MODELS.get(kind)
        if model and name.strip():
            last = db.query(func.max(model.position)).scalar()
            db.add(model(name=name.strip()[:60], color=_color(color, "#579BFC"), position=(last or 0) + 1))
            db.commit()
        return RedirectResponse("/tools/tasks#settings", status_code=303)

    @router.post("/options/{kind}/{option_id}")
    async def option_update(kind: str, option_id: int, request: Request, db: Session = Depends(get_db)):
        model = OPTION_MODELS.get(kind)
        option = db.get(model, option_id) if model else None
        if option:
            form = await request.form()
            if form.get("name", "").strip():
                option.name = form["name"].strip()[:60]
            option.color = _color(form.get("color"), option.color)
            if kind == "status":
                option.is_done = bool(form.get("is_done"))
            db.commit()
        return RedirectResponse("/tools/tasks#settings", status_code=303)

    @router.post("/options/{kind}/{option_id}/delete")
    def option_delete(kind: str, option_id: int, db: Session = Depends(get_db)):
        model = OPTION_MODELS.get(kind)
        option = db.get(model, option_id) if model else None
        if option:
            column = Task.status_id if kind == "status" else Task.label_id
            db.query(Task).filter(column == option.id).update({column: None})
            db.delete(option)
            db.commit()
        return RedirectResponse("/tools/tasks#settings", status_code=303)

    return router

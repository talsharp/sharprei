"""Tools › Tasks: a Monday-style board with weekday groups, statuses and labels.

Groups are fixed weekdays (Sunday first) plus "Later" - names, not dates, so a
task stays on its day until it's dragged elsewhere. Finished tasks aren't kept:
"Clear done" deletes them for good.
"""

from collections import OrderedDict

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Task, TaskLabel, TaskStatus

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

OPTION_MODELS = {"status": TaskStatus, "label": TaskLabel}


def _int_or_none(value):
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _color(value: str, fallback: str) -> str:
    value = (value or "").strip()
    return value if len(value) == 7 and value.startswith("#") else fallback


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/tools/tasks")

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
        statuses, labels = _options(db)
        tasks = db.query(Task).order_by(Task.position, Task.id).all()
        groups = OrderedDict((key, []) for key in DAYS)
        for t in tasks:
            groups.setdefault(t.day if t.day in DAYS else "later", []).append(t)
        return templates.TemplateResponse(
            "tools_tasks.html",
            {
                "request": request,
                "active": "tasks",
                "days": DAYS,
                "groups": groups,
                "statuses": statuses,
                "labels": labels,
                "done_count": sum(1 for t in tasks if t.status and t.status.is_done),
                "options_json": {
                    "status": [{"id": o.id, "name": o.name, "color": o.color, "done": o.is_done} for o in statuses],
                    "label": [{"id": o.id, "name": o.name, "color": o.color} for o in labels],
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
        done_ids = [s.id for s in db.query(TaskStatus).filter(TaskStatus.is_done.is_(True))]
        removed = 0
        if done_ids:
            for task in db.query(Task).filter(Task.status_id.in_(done_ids)).all():
                db.delete(task)
                removed += 1
            db.commit()
        return {"removed": removed}

    @router.post("/move")
    async def tasks_move(request: Request, db: Session = Depends(get_db)):
        """Body: {"day": "mon", "ids": [3, 1, 2]} - the new order of that group."""
        data = await request.json()
        day = data.get("day")
        if day not in DAYS:
            return Response(status_code=400)
        ids = [i for i in (_int_or_none(x) for x in data.get("ids", [])) if i]
        tasks = {t.id: t for t in db.query(Task).filter(Task.id.in_(ids)).all()}
        for position, task_id in enumerate(ids):
            task = tasks.get(task_id)
            if task:
                task.day = day
                task.position = position
        db.commit()
        return Response(status_code=204)

    @router.post("/{task_id}")
    async def task_update(task_id: int, request: Request, db: Session = Depends(get_db)):
        """Saves whichever fields were sent (title, description, status_id, label_id, day)."""
        task = db.get(Task, task_id)
        if not task:
            return Response(status_code=404)
        form = await request.form()
        if "title" in form and form["title"].strip():
            task.title = form["title"].strip()[:255]
        if "description" in form:
            task.description = form["description"].strip() or None
        if "status_id" in form:
            task.status_id = _int_or_none(form["status_id"])
        if "label_id" in form:
            task.label_id = _int_or_none(form["label_id"])
        if "day" in form and form["day"] in DAYS and form["day"] != task.day:
            last = db.query(func.max(Task.position)).filter(Task.day == form["day"]).scalar()
            task.day = form["day"]
            task.position = (last or 0) + 1
        db.commit()
        return Response(status_code=204)

    @router.post("/{task_id}/delete")
    def task_delete(task_id: int, db: Session = Depends(get_db)):
        task = db.get(Task, task_id)
        if task:
            db.delete(task)
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

"""Rentals › Projects (e.g. Warner Robins): payments ledger + loan terms.
Edits save field by field (no reload); the summary refreshes itself after
every change via the `project-changed` event."""

import csv
import io
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.finance import local_today
from app.models import Project, ProjectPayment
from app.projects import DEFAULT_ACCOUNTS, DEFAULT_PARTIES, PAYMENT_KINDS, summarize

CHANGED = {"HX-Trigger": "project-changed"}


def _money(value: str) -> Optional[float]:
    value = (value or "").replace(",", "").replace("$", "").strip()
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _date(value: str) -> Optional[date]:
    try:
        d = date.fromisoformat(value) if value else None
    except ValueError:
        return None
    return d if d is None or d.year >= 2000 else None  # ignore a half-typed year


def _text(value: str, limit: Optional[int] = None) -> Optional[str]:
    value = (value or "").strip()
    return (value[:limit] if limit else value) or None


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    def _context(request: Request, project: Project, db: Session) -> dict:
        used_accounts = {p.account for p in project.payments if p.account}
        used_parties = {x for p in project.payments for x in (p.paid_by, p.paid_to) if x}
        return {"request": request, "active": f"project-{project.id}", "project": project,
                "s": summarize(project, local_today()), "kinds": PAYMENT_KINDS,
                "accounts": sorted(set(DEFAULT_ACCOUNTS) | used_accounts),
                "parties": sorted(set(DEFAULT_PARTIES) | used_parties)}

    @router.get("/rentals/projects")
    def projects_list(request: Request, db: Session = Depends(get_db)):
        projects = db.query(Project).order_by(Project.name).all()
        return templates.TemplateResponse("projects_list.html", {"request": request, "active": "projects", "projects": projects})

    @router.post("/rentals/projects/new")
    def projects_new(name: str = Form(""), db: Session = Depends(get_db)):
        if not name.strip():
            return RedirectResponse("/rentals/projects", status_code=303)
        project = Project(name=name.strip()[:120], loan_rate=4.5)
        db.add(project)
        db.commit()
        return RedirectResponse(f"/rentals/projects/{project.id}", status_code=303)

    @router.get("/rentals/projects/{project_id}")
    def project_detail(request: Request, project_id: int, db: Session = Depends(get_db)):
        project = db.get(Project, project_id)
        if not project:
            return RedirectResponse("/rentals/projects", status_code=303)
        return templates.TemplateResponse("project_detail.html", _context(request, project, db))

    @router.get("/rentals/projects/{project_id}/summary")
    def project_summary(request: Request, project_id: int, part: str = "kpis", db: Session = Depends(get_db)):
        project = db.get(Project, project_id)
        if not project:
            return Response(status_code=404)
        return templates.TemplateResponse("_project_summary.html", {**_context(request, project, db), "part": part})

    @router.post("/rentals/projects/{project_id}")
    async def project_update(project_id: int, request: Request, db: Session = Depends(get_db)):
        project = db.get(Project, project_id)
        if not project:
            return Response(status_code=404)
        form = await request.form()
        if _text(form.get("name", "")):
            project.name = _text(form["name"], 120)
        for field, limit in (("properties", 255), ("partners", 120), ("notes", None)):
            if field in form:
                setattr(project, field, _text(form[field], limit))
        if "loan_rate" in form:
            project.loan_rate = _money(form["loan_rate"])
        if "loan_start" in form:
            project.loan_start = _date(form["loan_start"])
        db.commit()
        return Response(status_code=204, headers=CHANGED)

    @router.post("/rentals/projects/{project_id}/payments/new")
    async def payment_new(request: Request, project_id: int, db: Session = Depends(get_db)):
        project = db.get(Project, project_id)
        form = await request.form()
        amount = _money(form.get("amount", ""))
        if not project or amount is None:
            return Response(status_code=204)
        db.add(ProjectPayment(
            project_id=project.id, amount=amount, paid_on=_date(form.get("paid_on", "")),
            kind=form.get("kind") if form.get("kind") in PAYMENT_KINDS else "other",
            paid_by=_text(form.get("paid_by", ""), 80), paid_to=_text(form.get("paid_to", ""), 80),
            account=_text(form.get("account", ""), 120), description=_text(form.get("description", "")),
            needs_recheck=form.get("needs_recheck") == "1", netted=form.get("netted") == "1"))
        db.commit()
        db.refresh(project)
        return templates.TemplateResponse("_project_payment_rows.html", _context(request, project, db), headers=CHANGED)

    @router.post("/rentals/project-payments/{payment_id}")
    async def payment_update(payment_id: int, request: Request, db: Session = Depends(get_db)):
        p = db.get(ProjectPayment, payment_id)
        if not p:
            return Response(status_code=404)
        form = await request.form()
        if "amount" in form and _money(form["amount"]) is not None:
            p.amount = _money(form["amount"])
        if "paid_on" in form:
            day = _date(form["paid_on"])
            if day or not form["paid_on"]:
                p.paid_on = day
        if form.get("kind") in PAYMENT_KINDS:
            p.kind = form["kind"]
        for field, limit in (("paid_by", 80), ("paid_to", 80), ("account", 120), ("description", None)):
            if field in form:
                setattr(p, field, _text(form[field], limit))
        for flag in ("needs_recheck", "netted"):
            if flag in form:
                setattr(p, flag, form[flag] == "1")
        db.commit()
        return Response(status_code=204, headers=CHANGED)

    @router.post("/rentals/project-payments/{payment_id}/delete")
    def payment_delete(payment_id: int, db: Session = Depends(get_db)):
        p = db.get(ProjectPayment, payment_id)
        if p:
            db.delete(p)
            db.commit()
        return Response(content="", media_type="text/html", headers=CHANGED)

    @router.get("/rentals/projects/{project_id}/payments.csv")
    def payments_csv(project_id: int, db: Session = Depends(get_db)):
        """The ledger for the accountant."""
        project = db.get(Project, project_id)
        if not project:
            return Response(status_code=404)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Date", "Amount (USD)", "Type", "Paid by", "Paid to", "Account", "Description", "Needs recheck", "No money moved"])
        for p in project.payments:
            w.writerow([p.paid_on.isoformat() if p.paid_on else "", f"{float(p.amount or 0):.2f}",
                        PAYMENT_KINDS.get(p.kind, PAYMENT_KINDS["other"])["label"], p.paid_by or "", p.paid_to or "",
                        p.account or "", p.description or "", "Yes" if p.needs_recheck else "", "Yes" if p.netted else ""])
        name = "".join(c for c in project.name if c.isalnum() or c in " -_").strip().replace(" ", "_") or "project"
        return Response("﻿" + out.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}_payments_{local_today().isoformat()}.csv"'})

    return router

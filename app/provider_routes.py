"""Tools › Service Providers: people Tal worked with (each job dated, good or not good)
and people recommended to him, by type - so when he needs a plumber he sees who."""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ProviderJob, ProviderType, ServiceProvider

SECTIONS = {"worked": "Worked with", "recommended": "Recommended / to try"}
RATINGS = {"good": "Good", "bad": "Not good"}


def _type_names(db: Session, value: str) -> Optional[str]:
    """ "Contractor, plumbing, Roofing" -> the names as the type list has them; a new
    name becomes a type (so it's offered next time and gets a filter)."""
    existing = {t.name.lower(): t.name for t in db.query(ProviderType).all()}
    names = []
    for raw in value.split(","):
        name = raw.strip()[:60]
        if not name:
            continue
        if name.lower() not in existing:
            last = db.query(func.max(ProviderType.position)).scalar() or 0
            db.add(ProviderType(name=name, position=last + 1))
            db.flush()
            existing[name.lower()] = name
        if existing[name.lower()] not in names:
            names.append(existing[name.lower()])
    return ", ".join(names) or None


def _money(value: str) -> Optional[float]:
    try:
        return float(value.replace(",", "").replace("$", "").strip()) if value.strip() else None
    except ValueError:
        return None


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return date.today()


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter(prefix="/tools/providers")
    templates.env.globals["PROVIDER_RATINGS"] = RATINGS

    def _types(db: Session):
        return db.query(ProviderType).order_by(ProviderType.position, ProviderType.id).all()

    @router.get("")
    def providers_page(request: Request, db: Session = Depends(get_db)):
        providers = db.query(ServiceProvider).order_by(func.lower(ServiceProvider.name)).all()
        return templates.TemplateResponse("tools_providers.html", {
            "request": request, "active": "providers", "types": _types(db), "sections": SECTIONS,
            "groups": {key: [p for p in providers if p.section == key] for key in SECTIONS},
            "jobs": db.query(ProviderJob).order_by(ProviderJob.worked_on.desc(), ProviderJob.id.desc()).all(),
            "today": date.today().isoformat(),
        })

    @router.post("/new")
    def provider_new(
        name: str = Form(""), types: str = Form(""), section: str = Form("worked"), phone: str = Form(""),
        email: str = Form(""), facebook: str = Form(""), recommended_by: str = Form(""), notes: str = Form(""),
        worked_on: str = Form(""), rating: str = Form(""), work: str = Form(""), amount: str = Form(""),
        db: Session = Depends(get_db),
    ):
        if not name.strip():
            return RedirectResponse("/tools/providers", status_code=303)
        provider = ServiceProvider(
            name=name.strip()[:120], types=_type_names(db, types),
            section=section if section in SECTIONS else "worked", phone=phone.strip()[:40] or None,
            email=email.strip()[:200] or None, facebook=facebook.strip()[:255] or None,
            recommended_by=recommended_by.strip()[:120] or None, notes=notes.strip() or None,
        )
        db.add(provider)
        if provider.section == "worked" and (worked_on or rating or work.strip() or amount.strip()):
            provider.jobs.append(ProviderJob(worked_on=_date(worked_on), rating=rating if rating in RATINGS else None,
                                             work=work.strip()[:255] or None, amount=_money(amount)))
        db.commit()
        return RedirectResponse(f"/tools/providers#{provider.section}", status_code=303)

    @router.get("/{provider_id}")
    def provider_page(request: Request, provider_id: int, db: Session = Depends(get_db)):
        provider = db.get(ServiceProvider, provider_id)
        if not provider:
            return RedirectResponse("/tools/providers", status_code=303)
        return templates.TemplateResponse("tools_provider_edit.html", {
            "request": request, "active": "providers", "p": provider, "types": _types(db), "sections": SECTIONS,
            "today": date.today().isoformat(),
        })

    @router.post("/{provider_id}")
    def provider_save(
        provider_id: int, name: str = Form(""), types: str = Form(""), section: str = Form("worked"),
        phone: str = Form(""), email: str = Form(""), facebook: str = Form(""), recommended_by: str = Form(""),
        notes: str = Form(""), db: Session = Depends(get_db),
    ):
        provider = db.get(ServiceProvider, provider_id)
        if provider and name.strip():
            provider.name = name.strip()[:120]
            provider.types = _type_names(db, types)
            provider.section = section if section in SECTIONS else provider.section
            provider.phone = phone.strip()[:40] or None
            provider.email = email.strip()[:200] or None
            provider.facebook = facebook.strip()[:255] or None
            provider.recommended_by = recommended_by.strip()[:120] or None
            provider.notes = notes.strip() or None
            db.commit()
        return RedirectResponse(f"/tools/providers/{provider_id}?saved=1", status_code=303)

    @router.post("/{provider_id}/jobs")
    def job_add(provider_id: int, worked_on: str = Form(""), rating: str = Form(""), note: str = Form(""),
                work: str = Form(""), amount: str = Form(""), db: Session = Depends(get_db)):
        """A time Tal worked with them. Someone from Recommended moves to Worked with."""
        provider = db.get(ServiceProvider, provider_id)
        if provider:
            provider.jobs.append(ProviderJob(worked_on=_date(worked_on), rating=rating if rating in RATINGS else None,
                                             work=work.strip()[:255] or None, amount=_money(amount), note=note.strip() or None))
            provider.section = "worked"
            db.commit()
        return RedirectResponse(f"/tools/providers/{provider_id}#jobs", status_code=303)

    @router.post("/jobs/{job_id}")
    def job_save(job_id: int, worked_on: str = Form(""), rating: str = Form(""), note: str = Form(""),
                 work: str = Form(""), amount: str = Form(""), db: Session = Depends(get_db)):
        """Fixes a job: date, work, price, how it went, note."""
        job = db.get(ProviderJob, job_id)
        if not job:
            return RedirectResponse("/tools/providers", status_code=303)
        job.worked_on = _date(worked_on) if worked_on else job.worked_on
        job.work = work.strip()[:255] or None
        job.amount = _money(amount)
        job.rating = rating if rating in RATINGS else None
        job.note = note.strip() or None
        db.commit()
        return RedirectResponse(f"/tools/providers/{job.provider_id}#jobs", status_code=303)

    @router.post("/jobs/{job_id}/delete")
    def job_delete(job_id: int, db: Session = Depends(get_db)):
        job = db.get(ProviderJob, job_id)
        provider_id = job.provider_id if job else None
        if job:
            db.delete(job)
            db.commit()
        return RedirectResponse(f"/tools/providers/{provider_id}#jobs" if provider_id else "/tools/providers", status_code=303)

    @router.post("/{provider_id}/delete")
    def provider_delete(provider_id: int, db: Session = Depends(get_db)):
        provider = db.get(ServiceProvider, provider_id)
        if provider:
            db.delete(provider)
            db.commit()
        return RedirectResponse("/tools/providers", status_code=303)

    @router.post("/types/new")
    def type_new(name: str = Form(""), db: Session = Depends(get_db)):
        name = name.strip()[:60]
        if name and not db.query(ProviderType).filter(func.lower(ProviderType.name) == name.lower()).first():
            last = db.query(func.max(ProviderType.position)).scalar() or 0
            db.add(ProviderType(name=name, position=last + 1))
            db.commit()
        return RedirectResponse("/tools/providers#types", status_code=303)

    @router.post("/types/{type_id}/delete")
    def type_delete(type_id: int, db: Session = Depends(get_db)):
        """Only a type nobody uses can be removed."""
        kind = db.get(ProviderType, type_id)
        if kind and not any(kind.name.lower() in (t.lower() for t in p.type_list) for p in db.query(ServiceProvider).all()):
            db.delete(kind)
            db.commit()
        return RedirectResponse("/tools/providers#types", status_code=303)

    return router

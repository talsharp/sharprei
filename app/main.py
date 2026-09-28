import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import List

import pandas as pd
from fastapi import Depends, FastAPI, File, Form, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import config  # noqa: F401  (loads .env before anything else)
from app import audit  # noqa: F401  (registers the audit log listener)
from app import auth, scheduler
from app.database import backup_db, get_db
from app.documents import MAX_FILE_MB, folder_for, save_uploads, serve, too_big_suffix
from app.finance_routes import build_router as build_finance_router
from app.followups import get_follow_up_summaries, get_rounds_overview
from app.weekly_import import (
    METRIC_FIELDS,
    METRIC_LABELS,
    ROUND_LABELS,
    ROUND_ORDER,
    apply_plan,
    build_plan,
    read_workbook,
    save_workbook,
)
from app.metrics import get_zip_metrics
from app.models import (
    UNSET_DATE,
    AuditLog,
    CampaignRun,
    Deal,
    MonthlyExpense,
    Neighborhood,
    Property,
    PropertyFile,
    PropertyOwner,
    RenovationExpense,
    RunType,
    ZipCode,
    ZipNeighborhood,
    ZipStatus,
)
from app.rentals import (
    RENOVATION_UPLOAD_DIR,
    get_portfolio_summary,
    get_properties,
    get_property_yield,
    save_renovation_file,
)

app = FastAPI(title="SharpREI")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


def parse_month(value: str) -> date:
    """Parse a <input type=month> value ("YYYY-MM") into a date on the 1st of
    that month. Run dates are captured at month granularity only - see
    UNSET_DATE for the "not known at all" case."""
    if not value:
        return UNSET_DATE
    year, month = value.split("-")
    return date(int(year), int(month), 1)

STATUS_LABELS = {
    "not_tried": "Need to Run",
    "active": "Used",
    "watchlist": "Watchlist",
    "blacklist": "Blacklisted",
}
templates.env.globals["STATUS_LABELS"] = STATUS_LABELS
templates.env.globals["ZipStatus"] = ZipStatus
templates.env.globals["UNSET_DATE"] = UNSET_DATE
# Cache-buster for /static assets: derived from style.css's own mtime, so the
# browser always fetches fresh CSS after a deploy instead of serving a stale
# cached copy until the user manually hard-refreshes.
templates.env.globals["STATIC_VERSION"] = int(
    os.path.getmtime(Path(__file__).resolve().parent / "static" / "style.css")
)

app.include_router(build_finance_router(templates))
auth.install(app, templates)


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        return Response('{"status": "error"}', status_code=503, media_type="application/json")
    return {"status": "ok"}


@app.get("/health/backup")
def health_backup():
    """For uptime monitoring: 503 if the last nightly backup failed its restore
    test or hasn't run in over 36 hours."""
    from app.database import BACKUP_DIR

    status_file = BACKUP_DIR / "last_backup.json"
    try:
        status = json.loads(status_file.read_text())
        age_hours = (datetime.utcnow() - datetime.fromisoformat(status["at"])).total_seconds() / 3600
    except (OSError, ValueError, KeyError):
        return Response('{"backup": "never ran"}', status_code=503, media_type="application/json")
    ok = status.get("ok") and age_hours <= 36
    body = json.dumps({"backup": "ok" if ok else "problem", "hours_ago": round(age_hours, 1), "detail": status.get("message", "")})
    return Response(body, status_code=200 if ok else 503, media_type="application/json")


ACTIVITY_AREAS = {
    "zip_codes": "Zip codes",
    "campaign_runs": "Campaign runs",
    "zip_neighborhoods": "Zip neighborhoods",
    "neighborhoods": "Neighborhoods",
    "deals": "Zip deals",
    "properties": "Rentals - properties",
    "renovation_expenses": "Rentals - renovation",
    "monthly_expenses": "Rentals - monthly",
    "property_owners": "Rentals - owners",
    "finance_months": "Finance - months",
    "finance_expenses": "Finance - expenses",
    "finance_vendors": "Finance - vendors",
    "wholesale_deals": "Finance - deals",
    "users": "Users",
    "session": "Sign-ins",
}
ACTION_LABELS = {"create": "Added", "update": "Changed", "delete": "Deleted", "sign_in": "Signed in"}


@app.get("/activity")
def activity(request: Request, area: str = "", db: Session = Depends(get_db)):
    q = db.query(AuditLog)
    if area:
        q = q.filter(AuditLog.table_name == area)
    entries = q.order_by(AuditLog.id.desc()).limit(300).all()
    for e in entries:
        e.parsed = json.loads(e.changes) if e.changes else None
    return templates.TemplateResponse(
        "activity.html",
        {
            "request": request,
            "active": "activity",
            "entries": entries,
            "areas": ACTIVITY_AREAS,
            "area": area,
            "action_labels": ACTION_LABELS,
        },
    )


@app.on_event("startup")
def on_startup():
    # The schema is brought up to date by scripts/prestart.py (Alembic) before
    # the server starts - creating tables here could race ahead of a migration.
    scheduler.start()


SORT_FIELDS = {
    "zip": lambda m: m.zip_code.zip_code,
    "status": lambda m: m.zip_code.status,
    "sms": lambda m: m.total_sms,
    "reply_rate": lambda m: m.reply_rate,
    "drip_rate": lambda m: m.drip_rate,
    "warm_rate": lambda m: m.warm_rate,
    "lead_rate": lambda m: m.lead_rate,
    "leads": lambda m: m.total_leads,
    "signed_agreements": lambda m: m.total_signed_agreements,
    "deals": lambda m: m.deal_count,
    "profit": lambda m: m.total_profit,
    "score": lambda m: m.score if m.score is not None else -1,
    "region": lambda m: (m.zip_code.region_override or m.zip_code.region or ""),
}


@app.get("/")
def zip_list(request: Request, sort: str = "score", dir: str = "desc", db: Session = Depends(get_db)):
    metrics = get_zip_metrics(db)
    key_fn = SORT_FIELDS.get(sort, SORT_FIELDS["score"])
    metrics.sort(key=key_fn, reverse=(dir == "desc"))
    if sort == "score":
        # Zips with no sends have no score - keep them last in both directions.
        metrics.sort(key=lambda m: m.score is None)
    regions = sorted({m.zip_code.region_override or m.zip_code.region for m in metrics if (m.zip_code.region_override or m.zip_code.region)})
    return templates.TemplateResponse(
        "zip_list.html",
        {
            "request": request,
            "active": "list",
            "metrics": metrics,
            "regions": regions,
            "sort": sort,
            "dir": dir,
        },
    )


@app.get("/followups")
def followups_page(request: Request, db: Session = Depends(get_db)):
    round_numbers, rounds_by_number, round_summaries, summaries, combined_summary = get_rounds_overview(db)
    all_zips = db.query(ZipCode).all()
    regions = sorted({z.region_override or z.region for z in all_zips if (z.region_override or z.region)})
    return templates.TemplateResponse(
        "followups.html",
        {
            "request": request,
            "active": "followups",
            "round_numbers": round_numbers,
            "rounds_by_number": rounds_by_number,
            "round_summaries": round_summaries,
            "summaries": summaries,
            "combined_summary": combined_summary,
            "regions": regions,
        },
    )


@app.get("/zip/{zip_id}")
def zip_detail(request: Request, zip_id: int, db: Session = Depends(get_db)):
    metrics_list = get_zip_metrics(db, zip_code_id=zip_id)
    if not metrics_list:
        return RedirectResponse("/", status_code=303)
    metrics = metrics_list[0]
    runs = (
        db.query(CampaignRun)
        .filter(CampaignRun.zip_code_id == zip_id)
        .order_by(CampaignRun.run_date.desc())
        .all()
    )
    all_neighborhoods = db.query(Neighborhood).order_by(Neighborhood.name).all()
    follow_up = get_follow_up_summaries(db, [zip_id]).get(zip_id)
    all_regions = sorted({n.region for n in all_neighborhoods if n.region})
    return templates.TemplateResponse(
        "zip_detail.html",
        {
            "request": request,
            "active": "list",
            "m": metrics,
            "zc": metrics.zip_code,
            "runs": runs,
            "follow_up": follow_up,
            "all_neighborhoods": all_neighborhoods,
            "all_regions": all_regions,
            "run_types": list(RunType),
            "today": date.today().isoformat(),
        },
    )


@app.post("/zip/{zip_id}/update")
def zip_update(
    zip_id: int,
    status: str = Form(...),
    follow_up_status: str = Form(...),
    notes: str = Form(""),
    region_override: str = Form(""),
    db: Session = Depends(get_db),
):
    zc = db.get(ZipCode, zip_id)
    zc.status = status
    zc.follow_up_status = follow_up_status
    zc.notes = notes
    zc.region_override = region_override or None
    db.commit()
    return RedirectResponse(f"/zip/{zip_id}", status_code=303)


@app.post("/zip/{zip_id}/neighborhoods/add")
def add_neighborhood(zip_id: int, name: str = Form(...), db: Session = Depends(get_db)):
    name = name.strip()
    if name:
        neighborhood = db.query(Neighborhood).filter(Neighborhood.name == name).first()
        if not neighborhood:
            neighborhood = Neighborhood(name=name)
            db.add(neighborhood)
            db.flush()
        exists = (
            db.query(ZipNeighborhood)
            .filter_by(zip_code_id=zip_id, neighborhood_id=neighborhood.id)
            .first()
        )
        if not exists:
            has_any = db.query(ZipNeighborhood).filter_by(zip_code_id=zip_id).first() is not None
            db.add(
                ZipNeighborhood(
                    zip_code_id=zip_id,
                    neighborhood_id=neighborhood.id,
                    is_primary=not has_any,
                )
            )
        db.commit()
    return RedirectResponse(f"/zip/{zip_id}", status_code=303)


@app.post("/zip/{zip_id}/neighborhoods/{link_id}/set-main")
def set_main_neighborhood(zip_id: int, link_id: int, db: Session = Depends(get_db)):
    link = db.get(ZipNeighborhood, link_id)
    if link and link.zip_code_id == zip_id:
        db.query(ZipNeighborhood).filter(ZipNeighborhood.zip_code_id == zip_id).update({"is_primary": False})
        link.is_primary = True
        db.commit()
    return RedirectResponse(f"/zip/{zip_id}", status_code=303)


@app.post("/zip/{zip_id}/neighborhoods/{link_id}/remove")
def remove_neighborhood(zip_id: int, link_id: int, db: Session = Depends(get_db)):
    link = db.get(ZipNeighborhood, link_id)
    if link and link.zip_code_id == zip_id:
        was_primary = link.is_primary
        db.delete(link)
        db.flush()
        if was_primary:
            next_link = (
                db.query(ZipNeighborhood)
                .filter(ZipNeighborhood.zip_code_id == zip_id)
                .order_by(ZipNeighborhood.overlap_ratio.desc())
                .first()
            )
            if next_link:
                next_link.is_primary = True
        db.commit()
    return RedirectResponse(f"/zip/{zip_id}", status_code=303)


@app.post("/zip/{zip_id}/runs/new")
def create_run(
    zip_id: int,
    run_date: str = Form(""),
    run_type: str = Form(...),
    sms_sent: int = Form(0),
    replies: int = Form(0),
    leads: int = Form(0),
    warm: int = Form(0),
    drip: int = Form(0),
    signed_agreements: int = Form(0),
    opt_out: str = Form(""),
    db: Session = Depends(get_db),
):
    run = CampaignRun(
        zip_code_id=zip_id,
        run_date=parse_month(run_date),
        run_type=run_type,
        sms_sent=sms_sent,
        replies=replies,
        leads=leads,
        warm=warm,
        drip=drip,
        signed_agreements=signed_agreements,
        opt_out=int(opt_out) if opt_out.strip() else None,
    )
    db.add(run)
    zc = db.get(ZipCode, zip_id)
    if run_type == RunType.follow_up:
        if zc.follow_up_status == ZipStatus.not_tried:
            zc.follow_up_status = ZipStatus.active
    elif zc.status == ZipStatus.not_tried:
        zc.status = ZipStatus.active
    db.commit()
    return RedirectResponse(f"/zip/{zip_id}", status_code=303)


@app.post("/zip/{zip_id}/runs/{run_id}/update")
def update_run(
    zip_id: int,
    run_id: int,
    run_date: str = Form(""),
    run_type: str = Form(...),
    sms_sent: int = Form(0),
    replies: int = Form(0),
    leads: int = Form(0),
    warm: int = Form(0),
    drip: int = Form(0),
    signed_agreements: int = Form(0),
    opt_out: str = Form(""),
    db: Session = Depends(get_db),
):
    run = db.get(CampaignRun, run_id)
    if run and run.zip_code_id == zip_id:
        run.run_date = parse_month(run_date)
        run.run_type = run_type
        run.sms_sent = sms_sent
        run.replies = replies
        run.leads = leads
        run.warm = warm
        run.drip = drip
        run.signed_agreements = signed_agreements
        run.opt_out = int(opt_out) if opt_out.strip() else None
        db.commit()
    return RedirectResponse(f"/zip/{zip_id}", status_code=303)


@app.post("/zip/{zip_id}/runs/{run_id}/delete")
def delete_run(zip_id: int, run_id: int, db: Session = Depends(get_db)):
    run = db.get(CampaignRun, run_id)
    if run and run.zip_code_id == zip_id:
        db.delete(run)
        db.commit()
    return RedirectResponse(f"/zip/{zip_id}", status_code=303)


@app.post("/zip/{zip_id}/runs/{run_id}/deals/new")
def create_deal(
    zip_id: int,
    run_id: int,
    profit: float = Form(...),
    closed_date: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    deal = Deal(
        campaign_run_id=run_id,
        profit=profit,
        closed_date=date.fromisoformat(closed_date),
        notes=notes,
    )
    db.add(deal)
    db.commit()
    return RedirectResponse(f"/zip/{zip_id}", status_code=303)


@app.post("/zip/new")
def create_zip(zip_code: str = Form(...), db: Session = Depends(get_db)):
    zip_code = zip_code.strip()
    existing = db.query(ZipCode).filter(ZipCode.zip_code == zip_code).first()
    if not existing and zip_code:
        zc = ZipCode(zip_code=zip_code)
        db.add(zc)
        db.commit()
        db.refresh(zc)
        return RedirectResponse(f"/zip/{zc.id}", status_code=303)
    if existing:
        return RedirectResponse(f"/zip/{existing.id}", status_code=303)
    return RedirectResponse("/", status_code=303)


@app.get("/import/weekly")
def weekly_import_upload(request: Request):
    return templates.TemplateResponse(
        "weekly_import_upload.html", {"request": request, "active": "weekly_import", "error": None}
    )


@app.post("/import/weekly/preview")
async def weekly_import_preview(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    if not content:
        return templates.TemplateResponse(
            "weekly_import_upload.html",
            {"request": request, "active": "weekly_import", "error": "The file is empty"},
        )
    token = save_workbook(file.filename, content)
    try:
        sheets = read_workbook(token)
    except Exception as exc:
        return templates.TemplateResponse(
            "weekly_import_upload.html",
            {"request": request, "active": "weekly_import", "error": f"Could not read the file: {exc}"},
        )
    if not sheets:
        return templates.TemplateResponse(
            "weekly_import_upload.html",
            {
                "request": request,
                "active": "weekly_import",
                "error": (
                    "None of the expected tabs (First Text, Follow up 1/2/3) were found in this "
                    "workbook. Check the tab names match."
                ),
            },
        )

    changes = build_plan(db, sheets)
    changes = [c for c in changes if c.has_changes]
    by_round = {key: [c for c in changes if c.round_key == key] for key in ROUND_ORDER}
    summary = {
        key: {
            "update": sum(1 for c in by_round[key] if c.action == "update"),
            "create": sum(1 for c in by_round[key] if c.action == "create"),
            "skip": sum(1 for c in by_round[key] if c.action == "skip"),
        }
        for key in ROUND_ORDER
    }
    warnings = [c.warning for c in changes if c.warning]

    return templates.TemplateResponse(
        "weekly_import_preview.html",
        {
            "request": request,
            "active": "weekly_import",
            "token": token,
            "round_order": ROUND_ORDER,
            "round_labels": ROUND_LABELS,
            "by_round": by_round,
            "summary": summary,
            "warnings": warnings,
            "metric_fields": METRIC_FIELDS,
            "metric_labels": METRIC_LABELS,
        },
    )


@app.post("/import/weekly/commit")
async def weekly_import_commit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    token = form.get("token")
    sheets = read_workbook(token)
    changes = build_plan(db, sheets)
    changes = [c for c in changes if c.has_changes]

    backup_path = backup_db("weekly_import")

    applied = [c for c in changes if c.action in ("update", "create")]
    apply_plan(db, applied)
    db.commit()

    updated = sum(1 for c in applied if c.action == "update")
    created = sum(1 for c in applied if c.action == "create")
    skipped = sum(1 for c in changes if c.action == "skip")

    return templates.TemplateResponse(
        "weekly_import_result.html",
        {
            "request": request,
            "active": "weekly_import",
            "updated": updated,
            "created": created,
            "skipped": skipped,
            "backup_filename": backup_path.name,
        },
    )


@app.get("/rentals")
def rentals_list(request: Request, db: Session = Depends(get_db)):
    properties = get_properties(db)
    summary = get_portfolio_summary(properties)
    property_yields = {p.id: get_property_yield(p) for p in properties}
    owner_names = sorted({o.name for p in properties for o in p.owners})
    has_unassigned = any(not p.owners for p in properties)
    return templates.TemplateResponse(
        "rentals_list.html",
        {
            "request": request,
            "active": "rentals",
            "properties": properties,
            "summary": summary,
            "property_yields": property_yields,
            "owner_names": owner_names,
            "has_unassigned": has_unassigned,
        },
    )


@app.post("/rentals/new")
def create_property(address: str = Form(...), db: Session = Depends(get_db)):
    address = address.strip()
    if address:
        prop = Property(address=address)
        db.add(prop)
        db.commit()
        db.refresh(prop)
        return RedirectResponse(f"/rentals/{prop.id}", status_code=303)
    return RedirectResponse("/rentals", status_code=303)


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


@app.get("/rentals/{property_id}")
def property_detail(request: Request, property_id: int, db: Session = Depends(get_db)):
    prop = db.get(Property, property_id)
    if not prop:
        return RedirectResponse("/rentals", status_code=303)
    months = sorted(prop.monthly_expenses, key=lambda m: m.month, reverse=True)
    renovations = sorted(
        prop.renovation_expenses, key=lambda r: r.created_at, reverse=True
    )
    py = get_property_yield(prop)
    return templates.TemplateResponse(
        "property_detail.html",
        {
            "request": request,
            "active": "rentals",
            "prop": prop,
            "months": months,
            "renovations": renovations,
            "py": py,
            "today": date.today().isoformat(),
            "this_month": date.today().replace(day=1).strftime("%Y-%m"),
            "too_big": [n for n in request.query_params.get("too_big", "").split(",") if n],
            "max_mb": MAX_FILE_MB,
        },
    )


@app.post("/rentals/{property_id}/files")
async def property_files_upload(
    property_id: int,
    files: List[UploadFile] = File(...),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    prop = db.get(Property, property_id)
    if not prop:
        return RedirectResponse("/rentals", status_code=303)
    saved, too_big = await save_uploads(folder_for("properties", property_id), files)
    for f in saved:
        db.add(PropertyFile(property_id=property_id, note=note.strip()[:255] or None, **f))
    db.commit()
    return RedirectResponse(f"/rentals/{property_id}{too_big_suffix(too_big)}#files", status_code=303)


@app.get("/rentals/{property_id}/files/{file_id}")
def property_file(property_id: int, file_id: int, db: Session = Depends(get_db)):
    f = db.get(PropertyFile, file_id)
    if not f or f.property_id != property_id:
        return Response("File not found", status_code=404)
    return serve(folder_for("properties", property_id), f.stored_name, f.original_name)


@app.post("/rentals/{property_id}/files/{file_id}/delete")
def property_file_delete(property_id: int, file_id: int, db: Session = Depends(get_db)):
    f = db.get(PropertyFile, file_id)
    if f and f.property_id == property_id:
        (folder_for("properties", property_id) / f.stored_name).unlink(missing_ok=True)
        db.delete(f)
        db.commit()
    return RedirectResponse(f"/rentals/{property_id}#files", status_code=303)


@app.get("/rentals/{property_id}/report")
def property_report(request: Request, property_id: int, db: Session = Depends(get_db)):
    prop = db.get(Property, property_id)
    if not prop:
        return RedirectResponse("/rentals", status_code=303)
    months = sorted(prop.monthly_expenses, key=lambda m: m.month)
    renovations = sorted(prop.renovation_expenses, key=lambda r: r.created_at)
    py = get_property_yield(prop)
    cumulative_net_cash_flow = py.total_income - py.total_expenses
    owner_shares = [
        {
            "name": o.name,
            "percentage": float(o.percentage),
            "profit_share": (prop.estimated_profit * float(o.percentage) / 100) if prop.estimated_profit is not None else None,
            "cash_flow_share": cumulative_net_cash_flow * float(o.percentage) / 100,
        }
        for o in prop.owners
    ]
    note_categories = [
        ("utilities_note", "Utilities"),
        ("insurance_note", "Insurance"),
        ("repairs_note", "Repairs"),
        ("management_fees_note", "Mgmt Fees"),
        ("property_tax_note", "Prop. Tax"),
        ("other_note", "Other"),
    ]
    month_notes = []
    for m in months:
        for field, label in note_categories:
            note = getattr(m, field)
            if note:
                month_notes.append({"month": m.month.strftime("%b %Y"), "category": label, "note": note})
    return templates.TemplateResponse(
        "property_report.html",
        {
            "request": request,
            "prop": prop,
            "months": months,
            "renovations": renovations,
            "py": py,
            "cumulative_net_cash_flow": cumulative_net_cash_flow,
            "owner_shares": owner_shares,
            "month_notes": month_notes,
            "generated_at": datetime.now().strftime("%B %d, %Y"),
        },
    )


@app.post("/rentals/{property_id}/update")
def update_property(
    property_id: int,
    request: Request,
    address: str = Form(...),
    purchase_price: str = Form("0"),
    closing_costs: str = Form("0"),
    taxes_at_closing: str = Form("0"),
    insurance_at_closing: str = Form("0"),
    purchase_date: str = Form(""),
    max_arv: str = Form(""),
    estimated_value: str = Form(""),
    rent_price: str = Form(""),
    tenant_move_in_date: str = Form(""),
    account_balance: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    prop = db.get(Property, property_id)
    prop.address = address.strip()
    prop.purchase_price = float(purchase_price) if purchase_price.strip() else 0
    prop.closing_costs = float(closing_costs) if closing_costs.strip() else 0
    prop.taxes_at_closing = float(taxes_at_closing) if taxes_at_closing.strip() else 0
    prop.insurance_at_closing = float(insurance_at_closing) if insurance_at_closing.strip() else 0
    prop.purchase_date = date.fromisoformat(purchase_date) if purchase_date.strip() else None
    prop.max_arv = float(max_arv) if max_arv.strip() else None
    prop.estimated_value = float(estimated_value) if estimated_value.strip() else None
    prop.rent_price = float(rent_price) if rent_price.strip() else None
    prop.tenant_move_in_date = (
        date.fromisoformat(tenant_move_in_date) if tenant_move_in_date.strip() else None
    )
    prop.account_balance = float(account_balance) if account_balance.strip() else None
    prop.notes = notes
    db.commit()

    if _is_htmx(request):
        py = get_property_yield(prop)
        return templates.TemplateResponse(
            "_property_header.html", {"request": request, "prop": prop, "py": py}
        )
    return RedirectResponse(f"/rentals/{property_id}", status_code=303)


@app.post("/rentals/{property_id}/owners/new")
def add_owner(
    property_id: int,
    request: Request,
    name: str = Form(...),
    percentage: str = Form("0"),
    db: Session = Depends(get_db),
):
    name = name.strip()
    if name:
        owner = PropertyOwner(
            property_id=property_id,
            name=name,
            percentage=float(percentage) if percentage.strip() else 0,
        )
        db.add(owner)
        db.commit()

    if _is_htmx(request):
        prop = db.get(Property, property_id)
        return templates.TemplateResponse("_owners_card.html", {"request": request, "prop": prop})
    return RedirectResponse(f"/rentals/{property_id}", status_code=303)


@app.post("/rentals/{property_id}/owners/{owner_id}/delete")
def delete_owner(property_id: int, owner_id: int, request: Request, db: Session = Depends(get_db)):
    owner = db.get(PropertyOwner, owner_id)
    if owner and owner.property_id == property_id:
        db.delete(owner)
        db.commit()

    if _is_htmx(request):
        prop = db.get(Property, property_id)
        return templates.TemplateResponse("_owners_card.html", {"request": request, "prop": prop})
    return RedirectResponse(f"/rentals/{property_id}", status_code=303)


@app.post("/rentals/{property_id}/renovations/new")
async def add_renovation_expense(property_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    description = (form.get("description") or "").strip()
    cost = form.get("cost") or "0"
    contractor = (form.get("contractor") or "").strip()
    notes = form.get("notes") or ""
    upload = form.get("file")

    file_path = None
    file_original_name = None
    if upload is not None and getattr(upload, "filename", None):
        content = await upload.read()
        if content:
            file_path = save_renovation_file(upload.filename, content)
            file_original_name = upload.filename

    expense = None
    if description:
        expense = RenovationExpense(
            property_id=property_id,
            description=description,
            cost=float(cost) if cost.strip() else 0,
            contractor=contractor or None,
            notes=notes,
            file_path=file_path,
            file_original_name=file_original_name,
        )
        db.add(expense)
        db.commit()
        db.refresh(expense)

    if _is_htmx(request) and expense:
        prop = db.get(Property, property_id)
        return templates.TemplateResponse(
            "_reno_row_only.html", {"request": request, "r": expense, "prop": prop}
        )
    return RedirectResponse(f"/rentals/{property_id}", status_code=303)


@app.post("/rentals/{property_id}/renovations/{expense_id}/update")
async def update_renovation_expense(
    property_id: int, expense_id: int, request: Request, db: Session = Depends(get_db)
):
    form = await request.form()
    expense = db.get(RenovationExpense, expense_id)
    if expense and expense.property_id == property_id:
        expense.description = (form.get("description") or expense.description).strip()
        cost = form.get("cost")
        if cost and cost.strip():
            expense.cost = float(cost)
        expense.contractor = (form.get("contractor") or "").strip() or None
        expense.notes = form.get("notes") or ""

        upload = form.get("file")
        if upload is not None and getattr(upload, "filename", None):
            content = await upload.read()
            if content:
                expense.file_path = save_renovation_file(upload.filename, content)
                expense.file_original_name = upload.filename
        db.commit()

    if _is_htmx(request) and expense:
        prop = db.get(Property, property_id)
        return templates.TemplateResponse(
            "_reno_row_only.html", {"request": request, "r": expense, "prop": prop}
        )
    return RedirectResponse(f"/rentals/{property_id}", status_code=303)


@app.post("/rentals/{property_id}/renovations/{expense_id}/delete")
def delete_renovation_expense(
    property_id: int, expense_id: int, request: Request, db: Session = Depends(get_db)
):
    expense = db.get(RenovationExpense, expense_id)
    if expense and expense.property_id == property_id:
        db.delete(expense)
        db.commit()
    if _is_htmx(request):
        return Response(content="", media_type="text/html")
    return RedirectResponse(f"/rentals/{property_id}", status_code=303)


@app.get("/rentals/{property_id}/renovations/{expense_id}/file")
def download_renovation_file(property_id: int, expense_id: int, db: Session = Depends(get_db)):
    expense = db.get(RenovationExpense, expense_id)
    if not expense or expense.property_id != property_id or not expense.file_path:
        return RedirectResponse(f"/rentals/{property_id}", status_code=303)
    file_path = RENOVATION_UPLOAD_DIR / expense.file_path
    if not file_path.exists():
        return RedirectResponse(f"/rentals/{property_id}", status_code=303)
    return FileResponse(file_path, filename=expense.file_original_name or expense.file_path)


@app.post("/rentals/{property_id}/months/new")
def add_monthly_expense(
    property_id: int,
    request: Request,
    month: str = Form(...),
    income: str = Form(""),
    utilities: str = Form("0"),
    insurance: str = Form("0"),
    repairs: str = Form("0"),
    management_fees: str = Form("0"),
    property_tax: str = Form("0"),
    other: str = Form("0"),
    db: Session = Depends(get_db),
):
    prop = db.get(Property, property_id)
    month_date = parse_month(month)
    entry = None
    if month_date != UNSET_DATE:
        existing = (
            db.query(MonthlyExpense)
            .filter(MonthlyExpense.property_id == property_id, MonthlyExpense.month == month_date)
            .first()
        )
        if not existing:
            income_val = float(income) if income.strip() else (float(prop.rent_price) if prop.rent_price else 0)
            entry = MonthlyExpense(
                property_id=property_id,
                month=month_date,
                income=income_val,
                utilities=float(utilities) if utilities.strip() else 0,
                insurance=float(insurance) if insurance.strip() else 0,
                repairs=float(repairs) if repairs.strip() else 0,
                management_fees=float(management_fees) if management_fees.strip() else 0,
                property_tax=float(property_tax) if property_tax.strip() else 0,
                other=float(other) if other.strip() else 0,
            )
            db.add(entry)
            db.commit()
            db.refresh(entry)

    if _is_htmx(request) and entry:
        return templates.TemplateResponse(
            "_month_row_only.html", {"request": request, "m": entry, "prop": prop}
        )
    return RedirectResponse(f"/rentals/{property_id}", status_code=303)


@app.post("/rentals/{property_id}/months/{month_id}/update")
def update_monthly_expense(
    property_id: int,
    month_id: int,
    request: Request,
    income: str = Form("0"),
    utilities: str = Form("0"),
    utilities_note: str = Form(""),
    insurance: str = Form("0"),
    insurance_note: str = Form(""),
    repairs: str = Form("0"),
    repairs_note: str = Form(""),
    management_fees: str = Form("0"),
    management_fees_note: str = Form(""),
    property_tax: str = Form("0"),
    property_tax_note: str = Form(""),
    other: str = Form("0"),
    other_note: str = Form(""),
    db: Session = Depends(get_db),
):
    entry = db.get(MonthlyExpense, month_id)
    if entry and entry.property_id == property_id:
        entry.income = float(income) if income.strip() else 0
        entry.utilities = float(utilities) if utilities.strip() else 0
        entry.utilities_note = utilities_note.strip() or None
        entry.insurance = float(insurance) if insurance.strip() else 0
        entry.insurance_note = insurance_note.strip() or None
        entry.repairs = float(repairs) if repairs.strip() else 0
        entry.repairs_note = repairs_note.strip() or None
        entry.management_fees = float(management_fees) if management_fees.strip() else 0
        entry.management_fees_note = management_fees_note.strip() or None
        entry.property_tax = float(property_tax) if property_tax.strip() else 0
        entry.property_tax_note = property_tax_note.strip() or None
        entry.other = float(other) if other.strip() else 0
        entry.other_note = other_note.strip() or None
        db.commit()

    if _is_htmx(request) and entry:
        prop = db.get(Property, property_id)
        return templates.TemplateResponse(
            "_month_row_only.html", {"request": request, "m": entry, "prop": prop}
        )
    return RedirectResponse(f"/rentals/{property_id}", status_code=303)


@app.post("/rentals/{property_id}/months/{month_id}/delete")
def delete_monthly_expense(
    property_id: int, month_id: int, request: Request, db: Session = Depends(get_db)
):
    entry = db.get(MonthlyExpense, month_id)
    if entry and entry.property_id == property_id:
        db.delete(entry)
        db.commit()
    if _is_htmx(request):
        return Response(content="", media_type="text/html")
    return RedirectResponse(f"/rentals/{property_id}", status_code=303)

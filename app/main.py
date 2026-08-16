from datetime import date

import markdown as md
import pandas as pd
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

load_dotenv()

from app.database import get_db, init_db
from app.importer import (
    FIELD_LABELS,
    TARGET_FIELDS,
    classify_run_type,
    clean_zip,
    guess_mapping,
    parse_int,
    parse_optional_float,
    parse_optional_int,
    read_table,
    save_upload,
)
from app.followups import get_follow_up_summaries, get_rounds_overview
from app.insights import generate_insights
from app.metrics import TIER_ORDER, get_zip_metrics
from app.models import (
    UNSET_DATE,
    CampaignRun,
    Deal,
    ImportBatch,
    Neighborhood,
    RunType,
    ZipCode,
    ZipNeighborhood,
    ZipStatus,
)

app = FastAPI(title="AAA Houses - SMS")
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
    "not_tried": "Not Tried",
    "active": "Active",
    "watchlist": "Watchlist",
    "blacklist": "Blacklist",
}
templates.env.globals["STATUS_LABELS"] = STATUS_LABELS
templates.env.globals["ZipStatus"] = ZipStatus
templates.env.globals["TIER_ORDER"] = TIER_ORDER
templates.env.globals["UNSET_DATE"] = UNSET_DATE


@app.on_event("startup")
def on_startup():
    init_db()


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
    "score": lambda m: m.score,
    "tier": lambda m: len(TIER_ORDER) - TIER_ORDER.index(m.tier) if m.tier in TIER_ORDER else 0,
    "region": lambda m: (m.zip_code.region_override or m.zip_code.region or ""),
}


@app.get("/")
def zip_list(request: Request, sort: str = "score", dir: str = "desc", db: Session = Depends(get_db)):
    metrics = get_zip_metrics(db)
    key_fn = SORT_FIELDS.get(sort, SORT_FIELDS["score"])
    metrics.sort(key=key_fn, reverse=(dir == "desc"))
    follow_ups = get_follow_up_summaries(db, [m.zip_code.id for m in metrics])
    regions = sorted({m.zip_code.region_override or m.zip_code.region for m in metrics if (m.zip_code.region_override or m.zip_code.region)})
    return templates.TemplateResponse(
        "zip_list.html",
        {
            "request": request,
            "active": "list",
            "metrics": metrics,
            "follow_ups": follow_ups,
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
    avg_house_value: str = Form(""),
    tier_override: str = Form(""),
    region_override: str = Form(""),
    db: Session = Depends(get_db),
):
    zc = db.get(ZipCode, zip_id)
    zc.status = status
    zc.follow_up_status = follow_up_status
    zc.notes = notes
    zc.avg_house_value = float(avg_house_value) if avg_house_value.strip() else None
    zc.tier_override = tier_override or None
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


@app.get("/import")
def import_upload(request: Request):
    return templates.TemplateResponse(
        "import_upload.html", {"request": request, "active": "import", "error": None}
    )


@app.post("/import/preview")
async def import_preview(request: Request, file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        return templates.TemplateResponse(
            "import_upload.html",
            {"request": request, "active": "import", "error": "The file is empty"},
        )
    token = save_upload(file.filename, content)
    try:
        df = read_table(token)
    except Exception as exc:
        return templates.TemplateResponse(
            "import_upload.html",
            {"request": request, "active": "import", "error": f"Could not read the file: {exc}"},
        )
    columns = list(df.columns)
    mapping = guess_mapping(columns)
    preview_rows = df.head(10).fillna("").to_dict(orient="records")
    return templates.TemplateResponse(
        "import_preview.html",
        {
            "request": request,
            "active": "import",
            "token": token,
            "columns": columns,
            "mapping": mapping,
            "field_labels": FIELD_LABELS,
            "preview_rows": preview_rows,
            "row_count": len(df),
            "today": date.today().isoformat(),
        },
    )


@app.post("/import/commit")
async def import_commit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    token = form.get("token")
    default_run_type = form.get("default_run_type", "initial")
    default_date_val = form.get("default_date", "")
    try:
        default_date = parse_month(default_date_val)
    except ValueError:
        default_date = UNSET_DATE
    field_map = {field: form.get(f"map_{field}") or None for field in TARGET_FIELDS}

    if not field_map.get("zip_code"):
        return templates.TemplateResponse(
            "import_upload.html",
            {"request": request, "active": "import", "error": "You must map the zip code column before importing"},
        )

    df = read_table(token).fillna("")

    batch = ImportBatch(filename=token.split("_", 1)[-1], row_count=len(df), status="success")
    db.add(batch)
    db.flush()

    imported = 0
    failed = 0
    new_zips = 0
    errors = []
    zip_cache = {}

    for idx, row in df.iterrows():
        try:
            zip_code_val = clean_zip(row.get(field_map["zip_code"]))
            if not zip_code_val:
                raise ValueError("Missing zip code")

            if zip_code_val not in zip_cache:
                zc = db.query(ZipCode).filter(ZipCode.zip_code == zip_code_val).first()
                if not zc:
                    zc = ZipCode(zip_code=zip_code_val)
                    db.add(zc)
                    db.flush()
                    new_zips += 1
                zip_cache[zip_code_val] = zc
            zc = zip_cache[zip_code_val]
            zip_code_id = zc.id
            if zc.status == ZipStatus.not_tried:
                zc.status = ZipStatus.active

            avg_house_value_col = field_map.get("avg_house_value")
            if avg_house_value_col:
                parsed_value = parse_optional_float(row.get(avg_house_value_col))
                if parsed_value is not None:
                    zc.avg_house_value = parsed_value

            run_date_col = field_map.get("run_date")
            run_date_val = row.get(run_date_col) if run_date_col else ""
            parsed_date = pd.to_datetime(run_date_val, errors="coerce", dayfirst=False)
            run_date_final = parsed_date.date() if not pd.isna(parsed_date) else default_date
            run_type_col = field_map.get("run_type")
            run_type_val = classify_run_type(
                row.get(run_type_col) if run_type_col else None, default_run_type
            )

            run = CampaignRun(
                zip_code_id=zip_code_id,
                run_date=run_date_final,
                run_type=run_type_val,
                sms_sent=parse_int(row.get(field_map.get("sms_sent"))),
                replies=parse_int(row.get(field_map.get("replies"))),
                leads=parse_int(row.get(field_map.get("leads"))),
                warm=parse_int(row.get(field_map.get("warm"))),
                drip=parse_int(row.get(field_map.get("drip"))),
                signed_agreements=parse_int(row.get(field_map.get("signed_agreements"))),
                opt_out=parse_optional_int(row.get(field_map.get("opt_out"))) if field_map.get("opt_out") else None,
                import_batch_id=batch.id,
            )
            db.add(run)
            imported += 1
        except Exception as exc:
            failed += 1
            if len(errors) < 20:
                errors.append(f"Row {idx + 2}: {exc}")

    batch.status = "success" if failed == 0 else ("failed" if imported == 0 else "partial")
    db.commit()

    return templates.TemplateResponse(
        "import_result.html",
        {
            "request": request,
            "active": "import",
            "imported": imported,
            "failed": failed,
            "new_zips": new_zips,
            "errors": errors,
        },
    )


@app.get("/insights")
def insights_page(request: Request, db: Session = Depends(get_db)):
    metrics = get_zip_metrics(db)
    return templates.TemplateResponse(
        "insights.html",
        {
            "request": request,
            "active": "insights",
            "zip_count": len(metrics),
            "result_html": None,
            "error": None,
        },
    )


@app.post("/insights/run")
def insights_run(request: Request, db: Session = Depends(get_db)):
    metrics = get_zip_metrics(db)
    error = None
    result_html = None
    if not metrics:
        error = "No data yet to analyze. Add zip codes and runs before running insights."
    else:
        try:
            text = generate_insights(metrics)
            result_html = md.markdown(text)
        except Exception as exc:
            error = str(exc)
    return templates.TemplateResponse(
        "insights.html",
        {
            "request": request,
            "active": "insights",
            "zip_count": len(metrics),
            "result_html": result_html,
            "error": error,
        },
    )

"""Dispo › Buyers: the buyer list, and adding a suggested buyer to a deal."""

import csv
import io
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from openpyxl import load_workbook
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.buyer_activity import COLUMNS, buyer_activity, import_email_history, link_buyers_by_name
from app.buyers import CONTACT_CHANNELS, GRADES, PROPERTY_TYPES, Places, bed_range, join_list, split_list
from app.database import get_db
from app.models import Buyer, DealBuyer, DealBuyerContact, GhlEmail, GhlEmailRecipient, WholesaleDeal

MAX_STATS_BYTES = 10 * 1024 * 1024


def _read_rows(name: str, data: bytes) -> List[dict]:
    """The stats file's rows as dicts by header (Excel or CSV)."""
    if name.lower().endswith(".csv"):
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig", errors="replace"))))
    else:
        rows = [list(r) for r in load_workbook(io.BytesIO(data), read_only=True, data_only=True).worksheets[0].iter_rows(values_only=True)]
    rows = [r for r in rows if any(v not in (None, "") for v in r)]
    if not rows:
        return []
    header = [str(h or "").strip() for h in rows[0]]
    return [dict(zip(header, r)) for r in rows[1:]]


def _price(value: str) -> Optional[float]:
    value = (value or "").replace(",", "").replace("$", "").strip().lower()
    multiplier = 1000 if value.endswith("k") else 1
    try:
        return float(value.rstrip("k")) * multiplier if value else None
    except ValueError:
        return None


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()
    templates.env.globals["CONTACT_CHANNELS"] = CONTACT_CHANNELS
    templates.env.globals["split_list"] = split_list
    templates.env.globals["GRADES"] = GRADES
    templates.env.globals["bed_range"] = bed_range

    def _form(request: Request, db: Session, buyer: Optional[Buyer]):
        places = Places(db)
        return templates.TemplateResponse("dispo_buyer_edit.html", {
            "request": request, "active": "buyers", "buyer": buyer, "property_types": PROPERTY_TYPES,
            "regions": places.regions, "neighborhood_names": sorted(places.neighborhood_name.values()),
            "unknown": places.unknown(buyer) if buyer else [],
            "activity": buyer_activity(db, {buyer.id}).get(buyer.id) if buyer else None,
        })

    @router.get("/dispo/buyers")
    def buyers_list(request: Request, db: Session = Depends(get_db)):
        buyers = db.query(Buyer).order_by(Buyer.name).all()
        places = Places(db)
        return templates.TemplateResponse("dispo_buyers.html", {
            "request": request, "active": "buyers", "buyers": buyers,
            "unknown": {b.id: places.unknown(b) for b in buyers},
            "with_buybox": sum(1 for b in buyers if b.zip_codes or b.neighborhoods or b.areas),
            "activity": buyer_activity(db),
        })

    def _emails_page(request: Request, db: Session, result: Optional[dict] = None, error: Optional[str] = None):
        emails = db.query(GhlEmail).order_by(GhlEmail.sent_on.desc(), GhlEmail.id.desc()).all()
        return templates.TemplateResponse("dispo_email_history.html", {
            "request": request, "active": "buyers", "emails": emails, "result": result, "error": error, "columns": COLUMNS,
        })

    @router.get("/dispo/buyers/email-history")
    def email_history(request: Request, db: Session = Depends(get_db)):
        return _emails_page(request, db)

    @router.post("/dispo/buyers/email-history")
    async def email_history_upload(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
        data = await file.read(MAX_STATS_BYTES + 1)
        if not data or len(data) > MAX_STATS_BYTES:
            return _emails_page(request, db, error="That file is empty or over 10 MB.")
        try:
            rows = await run_in_threadpool(_read_rows, file.filename or "", data)
        except Exception:
            return _emails_page(request, db, error="That file couldn't be read - upload the Excel (.xlsx) or CSV export.")
        missing = [c for c in COLUMNS if rows and c not in rows[0]]
        if not rows or missing:
            return _emails_page(request, db, error="Missing columns: " + ", ".join(missing or COLUMNS))
        result = await run_in_threadpool(_import, rows)
        return _emails_page(request, db, result=result)

    def _import(rows: List[dict]) -> dict:
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            result = import_email_history(db, rows)
            db.commit()
            return result
        finally:
            db.close()

    @router.get("/dispo/buyers/new")
    def buyer_new_form(request: Request, db: Session = Depends(get_db)):
        return _form(request, db, None)

    @router.get("/dispo/buyers/{buyer_id}")
    def buyer_edit_form(request: Request, buyer_id: int, db: Session = Depends(get_db)):
        buyer = db.get(Buyer, buyer_id)
        if not buyer:
            return RedirectResponse("/dispo/buyers", status_code=303)
        return _form(request, db, buyer)

    @router.post("/dispo/buyers/save")
    def buyer_save(
        buyer_id: str = Form(""),
        name: str = Form(""),
        email: str = Form(""),
        phone: str = Form(""),
        channels: List[str] = Form([]),
        zip_codes: str = Form(""),
        neighborhoods: str = Form(""),
        areas: str = Form(""),
        max_price: str = Form(""),
        property_types: List[str] = Form([]),
        min_beds: str = Form(""),
        max_beds: str = Form(""),
        grades: List[str] = Form([]),
        exact_area_only: str = Form(""),
        notes: str = Form(""),
        db: Session = Depends(get_db),
    ):
        buyer = db.get(Buyer, int(buyer_id)) if buyer_id.isdigit() else None
        if not name.strip():
            return RedirectResponse(f"/dispo/buyers/{buyer.id}" if buyer else "/dispo/buyers/new", status_code=303)
        if not buyer:
            buyer = Buyer()
            db.add(buyer)
        buyer.name = name.strip()[:120]
        buyer.email = email.strip()[:200] or None
        buyer.phone = phone.strip()[:40] or None
        buyer.channels = ",".join(c for c in CONTACT_CHANNELS if c in channels) or "email"
        buyer.zip_codes = join_list(z for z in split_list(zip_codes))
        buyer.neighborhoods = join_list(split_list(neighborhoods))
        buyer.areas = join_list(split_list(areas))
        buyer.max_price = _price(max_price)
        buyer.property_types = join_list(t for t in PROPERTY_TYPES if t in property_types)
        buyer.min_beds = int(min_beds) if min_beds.strip().isdigit() else None
        buyer.max_beds = int(max_beds) if max_beds.strip().isdigit() else None
        buyer.grades = ",".join(g for g in GRADES if g in grades) or None
        buyer.exact_area_only = bool(exact_area_only)
        buyer.notes = notes.strip() or None
        db.flush()
        link_buyers_by_name(db)
        if buyer.email:  # their past GHL emails, if the stats came in before they were on the list
            db.query(GhlEmailRecipient).filter(GhlEmailRecipient.buyer_id.is_(None),
                                               GhlEmailRecipient.address == buyer.email.strip().lower()).update({GhlEmailRecipient.buyer_id: buyer.id})
        db.commit()
        return RedirectResponse(f"/dispo/buyers/{buyer.id}?saved=1", status_code=303)

    @router.post("/dispo/buyers/{buyer_id}/delete")
    def buyer_delete(buyer_id: int, db: Session = Depends(get_db)):
        buyer = db.get(Buyer, buyer_id)
        if buyer:
            db.query(DealBuyer).filter(DealBuyer.buyer_id == buyer.id).update({DealBuyer.buyer_id: None})
            db.delete(buyer)
            db.commit()
        return RedirectResponse("/dispo/buyers", status_code=303)

    @router.post("/finance/deals/{deal_id}/contacted/{buyer_id}")
    def contacted_set(deal_id: int, buyer_id: int, value: str = Form(""), db: Session = Depends(get_db)):
        """Contacted Yes / No for a buyer on a deal; empty clears it."""
        if not db.get(WholesaleDeal, deal_id) or not db.get(Buyer, buyer_id):
            return Response(status_code=404)
        row = db.query(DealBuyerContact).filter_by(deal_id=deal_id, buyer_id=buyer_id).first()
        if value in ("yes", "no"):
            if not row:
                row = DealBuyerContact(deal_id=deal_id, buyer_id=buyer_id)
                db.add(row)
            row.contacted = value == "yes"
        elif row:
            db.delete(row)
        db.commit()
        return Response(status_code=204)

    return router

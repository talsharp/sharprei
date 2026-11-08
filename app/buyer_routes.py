"""Dispo › Buyers: the buyer list, and adding a suggested buyer to a deal."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.buyers import CONTACT_CHANNELS, PROPERTY_TYPES, Places, join_list, split_list
from app.database import get_db
from app.models import Buyer, DealBuyer, WholesaleDeal


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

    def _form(request: Request, db: Session, buyer: Optional[Buyer]):
        places = Places(db)
        return templates.TemplateResponse("dispo_buyer_edit.html", {
            "request": request, "active": "buyers", "buyer": buyer, "property_types": PROPERTY_TYPES,
            "regions": places.regions, "neighborhood_names": sorted(places.neighborhood_name.values()),
            "unknown": places.unknown(buyer) if buyer else [],
        })

    @router.get("/dispo/buyers")
    def buyers_list(request: Request, db: Session = Depends(get_db)):
        buyers = db.query(Buyer).order_by(Buyer.name).all()
        places = Places(db)
        return templates.TemplateResponse("dispo_buyers.html", {
            "request": request, "active": "buyers", "buyers": buyers,
            "unknown": {b.id: places.unknown(b) for b in buyers},
            "with_buybox": sum(1 for b in buyers if b.zip_codes or b.neighborhoods or b.areas),
        })

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
        buyer.notes = notes.strip() or None
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

    @router.post("/finance/deals/{deal_id}/suggested/{buyer_id}")
    def suggested_add(deal_id: int, buyer_id: int, db: Session = Depends(get_db)):
        """Adds a suggested buyer to the deal's Buyers & walkthroughs."""
        deal, buyer = db.get(WholesaleDeal, deal_id), db.get(Buyer, buyer_id)
        if deal and buyer and not any(b.buyer_id == buyer.id for b in deal.buyers):
            db.add(DealBuyer(deal_id=deal.id, name=buyer.name, buyer_id=buyer.id))
            db.commit()
        return RedirectResponse(f"/finance/deals/{deal_id}#suggested", status_code=303)

    return router

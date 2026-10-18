"""Tools › Comps: compare "my house" with sold comps and settle on an ARV.

/c/<token> is a read-only page anyone with the link can open (no login), so
Tal can paste it into his other system. Turning the link off clears the token.
"""

import secrets
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import config
from app.comps import RENTCAST_FREE_CALLS, calls_this_month, fill_from_rentcast, parse_input, split_links
from app.database import get_db
from app.models import CompAnalysis, CompHouse

NUMBER_FIELDS = {"bedrooms": float, "bathrooms": float, "sqft": int, "year_built": int,
                 "sold_price": float, "days_on_market_manual": int}
DATE_FIELDS = {"sold_date", "listed_date"}
TEXT_FIELDS = {"address": 255, "zillow_url": 500, "pros": None, "cons": None}


def _number(value: str, kind):
    value = (value or "").replace(",", "").replace("$", "").strip()
    if not value:
        return None
    try:
        return kind(float(value))
    except ValueError:
        return None


def _date(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    def _usage(db: Session) -> dict:
        return {"configured": bool(config.RENTCAST_API_KEY), "calls": calls_this_month(db), "free": RENTCAST_FREE_CALLS}

    @router.get("/tools/comps")
    def comps_list(request: Request, db: Session = Depends(get_db)):
        analyses = db.query(CompAnalysis).order_by(CompAnalysis.updated_at.desc()).all()
        return templates.TemplateResponse(
            "comps_list.html",
            {"request": request, "active": "comps", "analyses": analyses, "usage": _usage(db),
             "error": request.query_params.get("error")},
        )

    @router.post("/tools/comps/new")
    def comps_new(link: str = Form(""), db: Session = Depends(get_db)):
        address, url = parse_input(link)
        if not address:
            return RedirectResponse("/tools/comps?error=address", status_code=303)
        analysis = CompAnalysis()
        db.add(analysis)
        house = CompHouse(analysis=analysis, is_subject=True, address=address, zillow_url=url)
        db.add(house)
        db.flush()
        fill_from_rentcast(db, house)
        # My house isn't sold - a "last sale" from public records would only confuse the table.
        house.sold_price = house.sold_date = house.listed_date = house.days_on_market_manual = None
        db.commit()
        return RedirectResponse(f"/tools/comps/{analysis.id}", status_code=303)

    @router.get("/tools/comps/{analysis_id}")
    def comps_detail(request: Request, analysis_id: int, db: Session = Depends(get_db)):
        analysis = db.get(CompAnalysis, analysis_id)
        if not analysis:
            return RedirectResponse("/tools/comps", status_code=303)
        share_url = f"{config.BASE_URL or str(request.base_url).rstrip('/')}/c/{analysis.share_token}" if analysis.share_token else None
        return templates.TemplateResponse(
            "comps_detail.html",
            {"request": request, "active": "comps", "a": analysis, "usage": _usage(db),
             "share_url": share_url, "error": request.query_params.get("error"), "public": False},
        )

    @router.post("/tools/comps/{analysis_id}")
    async def comps_update(analysis_id: int, request: Request, db: Session = Depends(get_db)):
        analysis = db.get(CompAnalysis, analysis_id)
        if not analysis:
            return Response(status_code=404)
        form = await request.form()
        for field in ("arv_low", "arv_avg", "arv_high"):
            if field in form:
                setattr(analysis, field, _number(form[field], float))
        if "notes" in form:
            analysis.notes = form["notes"].strip() or None
        db.commit()
        return Response(status_code=204)

    @router.post("/tools/comps/{analysis_id}/houses/new")
    def comps_add_houses(analysis_id: int, links: str = Form(""), db: Session = Depends(get_db)):
        """Adds every comp pasted at once (Zillow links, or one address per line)."""
        analysis = db.get(CompAnalysis, analysis_id)
        if not analysis:
            return RedirectResponse("/tools/comps", status_code=303)
        last = db.query(func.max(CompHouse.position)).filter(CompHouse.analysis_id == analysis_id).scalar() or 0
        existing = {h.zillow_url for h in analysis.houses if h.zillow_url}
        added = skipped = 0
        for item in split_links(links)[:20]:
            address, url = parse_input(item)
            if not address or (url and url in existing):
                skipped += 1
                continue
            last += 1
            house = CompHouse(analysis=analysis, address=address, zillow_url=url, position=last)
            db.add(house)
            db.flush()
            fill_from_rentcast(db, house)
            if url:
                existing.add(url)
            added += 1
        if added:
            analysis.updated_at = datetime.utcnow()
        db.commit()
        if not added:
            return RedirectResponse(f"/tools/comps/{analysis_id}?error=address#table", status_code=303)
        return RedirectResponse(f"/tools/comps/{analysis_id}?added={added}&skipped={skipped}#table", status_code=303)

    @router.post("/tools/comps/houses/{house_id}")
    async def comps_update_house(house_id: int, request: Request, db: Session = Depends(get_db)):
        """Saves whichever fields were sent; returns the recalculated days on market."""
        house = db.get(CompHouse, house_id)
        if not house:
            return Response(status_code=404)
        form = await request.form()
        for field, value in form.items():
            if field in NUMBER_FIELDS:
                setattr(house, field, _number(value, NUMBER_FIELDS[field]))
            elif field in DATE_FIELDS:
                setattr(house, field, _date(value))
            elif field in TEXT_FIELDS:
                limit = TEXT_FIELDS[field]
                text = value.strip()
                if field == "address" and not text:
                    continue
                setattr(house, field, (text[:limit] if limit else text) or None)
        house.analysis.updated_at = datetime.utcnow()
        db.commit()
        return {"days_on_market": house.days_on_market}

    @router.post("/tools/comps/houses/{house_id}/refresh")
    def comps_refresh_house(house_id: int, db: Session = Depends(get_db)):
        house = db.get(CompHouse, house_id)
        if not house:
            return RedirectResponse("/tools/comps", status_code=303)
        fill_from_rentcast(db, house, overwrite=True)
        if house.is_subject:
            house.sold_price = house.sold_date = house.listed_date = house.days_on_market_manual = None
        db.commit()
        return RedirectResponse(f"/tools/comps/{house.analysis_id}#table", status_code=303)

    @router.post("/tools/comps/houses/{house_id}/delete")
    def comps_delete_house(house_id: int, db: Session = Depends(get_db)):
        house = db.get(CompHouse, house_id)
        if not house or house.is_subject:
            return RedirectResponse("/tools/comps", status_code=303)
        analysis_id = house.analysis_id
        db.delete(house)
        db.commit()
        return RedirectResponse(f"/tools/comps/{analysis_id}#table", status_code=303)

    @router.post("/tools/comps/{analysis_id}/share")
    def comps_share(analysis_id: int, on: str = Form("1"), db: Session = Depends(get_db)):
        analysis = db.get(CompAnalysis, analysis_id)
        if analysis:
            analysis.share_token = secrets.token_urlsafe(16) if on == "1" else None
            db.commit()
        return RedirectResponse(f"/tools/comps/{analysis_id}", status_code=303)

    @router.post("/tools/comps/{analysis_id}/delete")
    def comps_delete(analysis_id: int, db: Session = Depends(get_db)):
        analysis = db.get(CompAnalysis, analysis_id)
        if analysis:
            db.delete(analysis)
            db.commit()
        return RedirectResponse("/tools/comps", status_code=303)

    # ---------- Public read-only page ----------

    @router.get("/c/{token}")
    def comps_public(request: Request, token: str, db: Session = Depends(get_db)):
        analysis = db.query(CompAnalysis).filter(CompAnalysis.share_token == token).first() if len(token) > 10 else None
        if not analysis:
            return templates.TemplateResponse("comps_public_missing.html", {"request": request}, status_code=404)
        return templates.TemplateResponse("comps_public.html", {"request": request, "a": analysis, "public": True})

    return router

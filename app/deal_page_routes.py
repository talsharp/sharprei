"""Deal landing pages: editor under Finance › Deals, public page at /d/<token>.

<token> is the general link or an investor's personal link; both show the
same page, and each visit is recorded against the link it came through.
"""

import shutil
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.comps import fill_from_county
from app.database import get_db
from app.deal_pages import (
    MAX_PHOTOS,
    VISITOR_COOKIE,
    ago,
    device_of,
    is_bot,
    link_stats,
    new_token,
    page_folder,
    public_base,
    save_contact_photo,
    save_photo,
    view_stats,
)
from app.models import CompHouse, DealBuyer, DealPage, DealPageLink, DealPageView, DealPhoto, WholesaleDeal

MAX_UPLOAD_BYTES = 30 * 1024 * 1024
TEXT_FIELDS = {"title": 255, "property_type": 60, "rent": 120, "walkthrough": 255,
               "contact_name": 120, "contact_phone": 40, "contact_email": 120, "description": None}
NUMBER_FIELDS = {"asking_price": float, "arv": float, "bedrooms": float, "bathrooms": float, "sqft": int, "year_built": int}


def _number(value: str, kind):
    value = (value or "").replace(",", "").replace("$", "").strip()
    try:
        return kind(float(value)) if value else None
    except ValueError:
        return None


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()
    templates.env.globals["ago"] = ago

    def _create_page(db: Session, deal: WholesaleDeal) -> DealPage:
        """New page, pre-filled: facts from county records, contact from the last page."""
        last = db.query(DealPage).order_by(DealPage.id.desc()).first()
        page = DealPage(deal_id=deal.id, token=new_token(), title=deal.address.split(",")[0],
                        contact_name=last.contact_name if last else "Tal Perry",
                        contact_phone=last.contact_phone if last else None,
                        contact_email=last.contact_email if last else None)
        db.add(page)
        db.flush()
        if last and last.contact_photo and (page_folder(last.id) / last.contact_photo).is_file():
            page_folder(page.id).mkdir(parents=True, exist_ok=True)
            shutil.copy(page_folder(last.id) / last.contact_photo, page_folder(page.id) / last.contact_photo)
            page.contact_photo = last.contact_photo
        probe = CompHouse(address=deal.address, is_subject=True)
        try:
            fill_from_county(probe)
        except Exception:
            pass
        page.bedrooms, page.bathrooms, page.sqft, page.year_built = probe.bedrooms, probe.bathrooms, probe.sqft, probe.year_built
        db.add(page)
        db.commit()
        return page

    def _editor(request: Request, db: Session, page: DealPage):
        base = public_base(request)
        names = [b.name for b in page.deal.buyers] + sorted({b.name for b in db.query(DealBuyer).all()}, key=str.lower)
        return templates.TemplateResponse(
            "deal_page_edit.html",
            {"request": request, "active": "fin_deals", "page": page, "deal": page.deal,
             "base": base, "stats": view_stats(page.views), "link_stats": link_stats(page),
             "buyer_names": list(dict.fromkeys(names)), "max_photos": MAX_PHOTOS,
             "error": request.query_params.get("error")},
        )

    def _page_for(db: Session, deal_id: int) -> Optional[DealPage]:
        return db.query(DealPage).filter(DealPage.deal_id == deal_id).first()

    # ---------- Editor ----------

    @router.get("/finance/deals/{deal_id}/page")
    def page_edit(request: Request, deal_id: int, db: Session = Depends(get_db)):
        deal = db.get(WholesaleDeal, deal_id)
        if not deal:
            return RedirectResponse("/finance/deals", status_code=303)
        page = _page_for(db, deal_id) or _create_page(db, deal)
        return _editor(request, db, page)

    @router.post("/finance/deals/{deal_id}/page")
    async def page_update(deal_id: int, request: Request, db: Session = Depends(get_db)):
        page = _page_for(db, deal_id)
        if not page:
            return Response(status_code=404)
        form = await request.form()
        for field, value in form.items():
            if field in TEXT_FIELDS:
                limit = TEXT_FIELDS[field]
                text = value.strip()
                if field == "title" and not text:
                    continue
                setattr(page, field, (text[:limit] if limit else text) or None)
            elif field in NUMBER_FIELDS:
                setattr(page, field, _number(value, NUMBER_FIELDS[field]))
        db.commit()
        return Response(status_code=204)

    @router.post("/finance/deals/{deal_id}/page/publish")
    def page_publish(deal_id: int, on: str = Form("1"), db: Session = Depends(get_db)):
        page = _page_for(db, deal_id)
        if page:
            page.published = on == "1"
            db.commit()
        return RedirectResponse(f"/finance/deals/{deal_id}/page", status_code=303)

    @router.post("/finance/deals/{deal_id}/page/photos")
    async def page_photos(deal_id: int, files: List[UploadFile] = File(...), db: Session = Depends(get_db)):
        page = _page_for(db, deal_id)
        if not page:
            return RedirectResponse("/finance/deals", status_code=303)
        position = (db.query(func.max(DealPhoto.position)).filter(DealPhoto.page_id == page.id).scalar() or 0)
        skipped = 0
        for upload in files:
            if len(page.photos) >= MAX_PHOTOS:
                skipped += 1
                continue
            data = await upload.read(MAX_UPLOAD_BYTES + 1)
            name = save_photo(page.id, data) if data and len(data) <= MAX_UPLOAD_BYTES else None
            if not name:
                skipped += 1
                continue
            position += 1
            page.photos.append(DealPhoto(stored_name=name, position=position))
        db.commit()
        suffix = f"?error=photos{skipped}" if skipped else ""
        return RedirectResponse(f"/finance/deals/{deal_id}/page{suffix}#photos", status_code=303)

    @router.post("/finance/deal-photos/{photo_id}/cover")
    def photo_cover(photo_id: int, db: Session = Depends(get_db)):
        photo = db.get(DealPhoto, photo_id)
        if not photo:
            return RedirectResponse("/finance/deals", status_code=303)
        page = photo.page
        others = [p for p in page.photos if p.id != photo.id]
        for i, p in enumerate([photo] + others):
            p.position = i
        db.commit()
        return RedirectResponse(f"/finance/deals/{page.deal_id}/page#photos", status_code=303)

    @router.post("/finance/deal-photos/{photo_id}/delete")
    def photo_delete(photo_id: int, db: Session = Depends(get_db)):
        photo = db.get(DealPhoto, photo_id)
        if not photo:
            return RedirectResponse("/finance/deals", status_code=303)
        deal_id = photo.page.deal_id
        (page_folder(photo.page_id) / photo.stored_name).unlink(missing_ok=True)
        db.delete(photo)
        db.commit()
        return RedirectResponse(f"/finance/deals/{deal_id}/page#photos", status_code=303)

    @router.get("/finance/deal-photos/{photo_id}")
    def photo_private(photo_id: int, db: Session = Depends(get_db)):
        """For the editor and preview (works while the page is still a draft)."""
        photo = db.get(DealPhoto, photo_id)
        if not photo:
            return Response(status_code=404)
        return FileResponse(page_folder(photo.page_id) / photo.stored_name, media_type="image/jpeg")

    @router.post("/finance/deals/{deal_id}/page/links")
    def link_new(deal_id: int, name: str = Form(""), db: Session = Depends(get_db)):
        page = _page_for(db, deal_id)
        if page and name.strip():
            page.links.append(DealPageLink(name=name.strip()[:120], token=new_token()))
            db.commit()
        return RedirectResponse(f"/finance/deals/{deal_id}/page#links", status_code=303)

    @router.post("/finance/deal-links/{link_id}/delete")
    def link_delete(link_id: int, db: Session = Depends(get_db)):
        link = db.get(DealPageLink, link_id)
        if not link:
            return RedirectResponse("/finance/deals", status_code=303)
        deal_id = link.page.deal_id
        db.delete(link)
        db.commit()
        return RedirectResponse(f"/finance/deals/{deal_id}/page#links", status_code=303)

    @router.post("/finance/deals/{deal_id}/page/photos/order")
    async def photos_order(deal_id: int, request: Request, db: Session = Depends(get_db)):
        """Body: {"ids": [photo ids in the new order]} - the first is the cover."""
        page = _page_for(db, deal_id)
        if not page:
            return Response(status_code=404)
        ids = [int(x) for x in (await request.json()).get("ids", []) if str(x).isdigit()]
        by_id = {p.id: p for p in page.photos}
        ordered = [by_id[i] for i in ids if i in by_id] + [p for p in page.photos if p.id not in ids]
        for position, photo in enumerate(ordered):
            photo.position = position
        db.commit()
        return Response(status_code=204)

    @router.post("/finance/deals/{deal_id}/page/contact-photo")
    async def contact_photo_upload(deal_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
        page = _page_for(db, deal_id)
        if not page:
            return RedirectResponse("/finance/deals", status_code=303)
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        name = save_contact_photo(page.id, data) if data and len(data) <= MAX_UPLOAD_BYTES else None
        if not name:
            return RedirectResponse(f"/finance/deals/{deal_id}/page?error=contact#details", status_code=303)
        if page.contact_photo:
            (page_folder(page.id) / page.contact_photo).unlink(missing_ok=True)
        page.contact_photo = name
        db.commit()
        return RedirectResponse(f"/finance/deals/{deal_id}/page#details", status_code=303)

    @router.post("/finance/deals/{deal_id}/page/contact-photo/delete")
    def contact_photo_delete(deal_id: int, db: Session = Depends(get_db)):
        page = _page_for(db, deal_id)
        if page and page.contact_photo:
            (page_folder(page.id) / page.contact_photo).unlink(missing_ok=True)
            page.contact_photo = None
            db.commit()
        return RedirectResponse(f"/finance/deals/{deal_id}/page#details", status_code=303)

    @router.get("/finance/deals/{deal_id}/page/contact-photo")
    def contact_photo_private(deal_id: int, db: Session = Depends(get_db)):
        page = _page_for(db, deal_id)
        if not page or not page.contact_photo:
            return Response(status_code=404)
        return FileResponse(page_folder(page.id) / page.contact_photo, media_type="image/jpeg")

    @router.get("/finance/deals/{deal_id}/page/preview")
    def page_preview(request: Request, deal_id: int, db: Session = Depends(get_db)):
        page = _page_for(db, deal_id)
        if not page:
            return RedirectResponse(f"/finance/deals/{deal_id}/page", status_code=303)
        return templates.TemplateResponse(
            "deal_page_public.html",
            {"request": request, "page": page, "preview": True, "base": public_base(request), "view_id": None,
             "photo_url": lambda p: f"/finance/deal-photos/{p.id}",
             "contact_photo_url": f"/finance/deals/{deal_id}/page/contact-photo"},
        )

    # ---------- Public ----------

    def _resolve(db: Session, token: str):
        """(page, link) for a general or personal token; page None if not shareable."""
        if not token or len(token) < 8:
            return None, None
        page = db.query(DealPage).filter(DealPage.token == token).first()
        link = None
        if not page:
            link = db.query(DealPageLink).filter(DealPageLink.token == token).first()
            page = link.page if link else None
        if not page or not page.published:
            return None, None
        return page, link

    @router.get("/d/{token}")
    def page_public(request: Request, token: str, db: Session = Depends(get_db)):
        page, link = _resolve(db, token)
        if not page:
            return templates.TemplateResponse("deal_page_missing.html", {"request": request}, status_code=404)
        user_agent = request.headers.get("user-agent", "")
        view, visitor = None, None
        # Not counted: link-preview bots, and you yourself (logged in on this address).
        if not is_bot(user_agent) and not request.session.get("user_id"):
            visitor = request.cookies.get(VISITOR_COOKIE)
            if not visitor or len(visitor) > 40:
                visitor = uuid4().hex
            seen = db.query(DealPageView.id).filter(DealPageView.page_id == page.id, DealPageView.visitor_id == visitor).first()
            view = DealPageView(page_id=page.id, link_id=link.id if link else None, visitor_id=visitor,
                                is_return=seen is not None, device=device_of(user_agent), created_at=datetime.utcnow())
            db.add(view)
            db.commit()
        response = templates.TemplateResponse(
            "deal_page_public.html",
            {"request": request, "page": page, "preview": False, "base": public_base(request), "token": token,
             "view_id": view.id if view else None,
             "photo_url": lambda p: f"/d/{token}/p/{p.id}",
             "contact_photo_url": f"/d/{token}/contact"},
        )
        if view:
            response.set_cookie(VISITOR_COOKIE, visitor, max_age=400 * 24 * 3600, path="/d/",
                                httponly=True, samesite="lax", secure=request.url.scheme == "https")
        return response

    @router.post("/d/{token}/seen")
    async def photos_seen(token: str, request: Request, db: Session = Depends(get_db)):
        """The photo viewer reports how many different photos this visit opened."""
        page, _ = _resolve(db, token)
        form = await request.form()
        view = db.get(DealPageView, int(form.get("view", "0"))) if page and str(form.get("view", "")).isdigit() else None
        # Only the visitor who made the view can update it (their own cookie).
        if view and view.page_id == page.id and view.visitor_id == request.cookies.get(VISITOR_COOKIE):
            count = min(int(form.get("count", "0")) if str(form.get("count", "")).isdigit() else 0, len(page.photos))
            if count > view.photos_seen:
                view.photos_seen = count
                db.commit()
        return Response(status_code=204)

    @router.get("/d/{token}/contact")
    def contact_photo_public(token: str, db: Session = Depends(get_db)):
        page, _ = _resolve(db, token)
        if not page or not page.contact_photo:
            return Response(status_code=404)
        return FileResponse(page_folder(page.id) / page.contact_photo, media_type="image/jpeg",
                            headers={"Cache-Control": "public, max-age=86400"})

    @router.get("/d/{token}/p/{photo_id}")
    def photo_public(token: str, photo_id: int, db: Session = Depends(get_db)):
        page, _ = _resolve(db, token)
        photo = db.get(DealPhoto, photo_id) if page else None
        if not photo or photo.page_id != page.id:
            return Response(status_code=404)
        return FileResponse(page_folder(page.id) / photo.stored_name, media_type="image/jpeg",
                            headers={"Cache-Control": "public, max-age=86400"})

    return router

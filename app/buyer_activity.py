"""
Where each buyer sees Tal's deals, and how each deal was seen - in two places:

- GHL: the deal emails (stats file Tal uploads after each email). Received /
  opened / clicked, counted per deal - a buyer who got two emails about one deal
  received one deal.
- SharpREI: the deal's landing page through the buyer's personal link.
  Received = a personal link was made for them; opened = they really viewed the
  page; clicked = they opened its photos.
"""

import re
from collections import defaultdict
from datetime import date, datetime
from typing import Dict, Iterable, Optional

from sqlalchemy.orm import Session

from app.deal_pages import is_real, view_stats
from app.models import Buyer, DealPage, DealPageLink, GhlEmail, GhlEmailRecipient, WholesaleDeal

COLUMNS = ["Deal address", "Email subject", "Date sent", "Buyer name", "Buyer email", "Opened", "Clicked"]


def street_key(address: str) -> str:
    """ "606 McCandless Ave, Pittsburgh, PA" -> "606 mccandless ave" """
    return re.sub(r"[^a-z0-9 ]", "", str(address or "").lower().split(",")[0]).strip()


def _yes(value) -> bool:
    return str(value or "").strip().lower() in ("yes", "y", "true", "1")


def _date(value) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def import_email_history(db: Session, rows: Iterable[dict]) -> dict:
    """Adds the stats file's rows. Running it again with newer stats updates
    them; an open or a click is never taken back. Fills a deal's neighborhood
    from the file only where it's empty."""
    deals = {street_key(d.address): d for d in db.query(WholesaleDeal).all()}
    by_email = {b.email.strip().lower(): b for b in db.query(Buyer).filter(Buyer.email.isnot(None))}
    by_name = {b.name.strip().lower(): b for b in db.query(Buyer).all()}
    emails = {(e.address.strip().lower(), e.subject, e.sent_on): e for e in db.query(GhlEmail).all()}
    summary = {"rows": 0, "new_emails": 0, "added": 0, "updated": 0, "no_deal": set(), "no_buyer": set()}
    for row in rows:
        address, subject, who = str(row.get("Deal address") or "").strip(), str(row.get("Email subject") or "").strip(), str(row.get("Buyer email") or "").strip().lower()
        if not address or not who:
            continue
        summary["rows"] += 1
        sent_on = _date(row.get("Date sent"))
        deal = deals.get(street_key(address))
        if not deal:
            summary["no_deal"].add(address)
        elif not deal.neighborhood and str(row.get("Neighborhood") or "").strip():
            deal.neighborhood = str(row["Neighborhood"]).strip()[:120]
        key = (address.lower(), subject, sent_on)
        email = emails.get(key)
        if not email:
            email = GhlEmail(deal_id=deal.id if deal else None, address=address[:255], subject=subject[:255] or "(no subject)", sent_on=sent_on)
            db.add(email)
            emails[key] = email
            summary["new_emails"] += 1
        name = str(row.get("Buyer name") or "").strip()
        buyer = by_email.get(who) or by_name.get(name.lower())
        if not buyer:
            summary["no_buyer"].add(who)
        recipient = next((r for r in email.recipients if r.address == who), None)
        if not recipient:
            recipient = GhlEmailRecipient(address=who[:200], name=name[:120] or None)
            email.recipients.append(recipient)
            summary["added"] += 1
        else:
            summary["updated"] += 1
        recipient.buyer_id = buyer.id if buyer else recipient.buyer_id
        recipient.opened = recipient.opened or _yes(row.get("Opened"))
        recipient.clicked = recipient.clicked or _yes(row.get("Clicked"))
        recipient.status = str(row.get("GHL status") or "").strip()[:30] or recipient.status
    db.flush()
    summary["no_deal"], summary["no_buyer"] = sorted(summary["no_deal"]), len(summary["no_buyer"])
    return summary


def link_buyers_by_name(db: Session) -> int:
    """Personal links whose name is exactly a buyer's name get linked to that buyer."""
    by_name = {b.name.strip().lower(): b.id for b in db.query(Buyer).all()}
    linked = 0
    for link in db.query(DealPageLink).filter(DealPageLink.buyer_id.is_(None)):
        buyer_id = by_name.get(link.name.strip().lower())
        if buyer_id:
            link.buyer_id = buyer_id
            linked += 1
    return linked


def _empty() -> dict:
    return {"ghl": {"deals": 0, "opened": 0, "clicked": 0, "emails": 0},
            "sharp": {"deals": 0, "opened": 0, "clicked": 0},
            "unsubscribed": False, "deals": {}}


def buyer_activity(db: Session, buyer_ids: Optional[set] = None) -> Dict[int, dict]:
    """Per buyer: GHL and SharpREI totals, and per deal what happened where."""
    out: Dict[int, dict] = defaultdict(_empty)
    q = db.query(GhlEmailRecipient).filter(GhlEmailRecipient.buyer_id.isnot(None))
    if buyer_ids is not None:
        q = q.filter(GhlEmailRecipient.buyer_id.in_(buyer_ids))
    for r in q.all():
        a = out[r.buyer_id]
        e = r.email
        key = e.deal_id or ("addr", street_key(e.address))
        d = a["deals"].setdefault(key, {"deal": e.deal, "address": e.address, "ghl_emails": 0, "ghl_opened": False,
                                         "ghl_clicked": False, "sharp": False, "sharp_opened": False, "sharp_clicked": False, "last": None})
        d["ghl_emails"] += 1
        d["ghl_opened"] = d["ghl_opened"] or r.opened
        d["ghl_clicked"] = d["ghl_clicked"] or r.clicked
        d["last"] = max(filter(None, [d["last"], e.sent_on]), default=None)
        a["ghl"]["emails"] += 1
        a["unsubscribed"] = a["unsubscribed"] or (r.status or "").lower() == "unsubscribed"
    q = db.query(DealPageLink).filter(DealPageLink.buyer_id.isnot(None))
    if buyer_ids is not None:
        q = q.filter(DealPageLink.buyer_id.in_(buyer_ids))
    for link in q.all():
        a = out[link.buyer_id]
        deal = link.page.deal
        d = a["deals"].setdefault(deal.id, {"deal": deal, "address": deal.address, "ghl_emails": 0, "ghl_opened": False,
                                            "ghl_clicked": False, "sharp": False, "sharp_opened": False, "sharp_clicked": False, "last": None})
        real = [v for v in link.views if is_real(v)]
        d["sharp"] = True
        d["sharp_opened"] = d["sharp_opened"] or bool(real)
        d["sharp_clicked"] = d["sharp_clicked"] or any(v.photos_seen for v in real)
        seen = max((v.created_at.date() for v in real), default=link.created_at.date())
        d["last"] = max(filter(None, [d["last"], seen]), default=None)
    for a in out.values():
        for d in a["deals"].values():
            if d["ghl_emails"]:
                a["ghl"]["deals"] += 1
                a["ghl"]["opened"] += d["ghl_opened"]
                a["ghl"]["clicked"] += d["ghl_clicked"]
            if d["sharp"]:
                a["sharp"]["deals"] += 1
                a["sharp"]["opened"] += d["sharp_opened"]
                a["sharp"]["clicked"] += d["sharp_clicked"]
    return out


def deal_reach(db: Session, deal: WholesaleDeal) -> dict:
    """How a deal was seen: each GHL email, and the landing page."""
    emails = []
    clicked_buyers = {}
    for e in db.query(GhlEmail).filter(GhlEmail.deal_id == deal.id).order_by(GhlEmail.sent_on, GhlEmail.id).all():
        rs = e.recipients
        emails.append({"email": e, "sent": len(rs), "opened": sum(r.opened for r in rs), "clicked": sum(r.clicked for r in rs)})
        for r in rs:
            if r.clicked:
                clicked_buyers[r.buyer_id or r.address] = r
    page = db.query(DealPage).filter(DealPage.deal_id == deal.id).first()
    stats = view_stats(page.views) if page else None
    all_recipients = {r.address for e in emails for r in e["email"].recipients}
    return {
        "emails": emails,
        "ghl": {"sent": len(all_recipients),
                "opened": len({r.address for e in emails for r in e["email"].recipients if r.opened}),
                "clicked": len(clicked_buyers)},
        "page": page,
        "sharp": stats,
        "links": len(page.links) if page else 0,
        "clicked": sorted(clicked_buyers.values(), key=lambda r: (r.name or r.address).lower()),
    }

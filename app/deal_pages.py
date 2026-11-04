"""
Deal landing pages: a public page per deal (photos, price, facts, contact)
shared by link - one general link plus a personal link per investor, so the
views show who looked.

Visits: link-preview fetchers (WhatsApp, iMessage, Facebook...) and other bots
that say what they are aren't recorded. Every other open is recorded, but only
counts once the page really was on screen for a moment - automatic link checkers
look like browsers, yet they never show the page. Opens of the same link within
30 minutes are one visit; someone who returns later "came back". One person is
one personal link or, on the general link, one browser (an anonymous cookie).
"""

import io
import re
import secrets
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from uuid import uuid4

from PIL import Image, ImageOps

from app import config
from app.database import UPLOAD_ROOT

try:  # iPhone photos (HEIC)
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:  # pragma: no cover
    pass

PHOTO_MAX_SIDE = 1800
PHOTO_QUALITY = 82
MAX_PHOTOS = 60
VISITOR_COOKIE = "sr_visitor"
SAME_VISIT = timedelta(minutes=30)

BOT_PATTERN = re.compile(
    r"bot|crawl|spider|preview|facebookexternalhit|whatsapp|telegram|slack|discord|skype|"
    r"linkedin|pinterest|embedly|vkshare|bitly|curl|wget|python-|headless|lighthouse",
    re.I,
)


def new_token() -> str:
    return secrets.token_urlsafe(9)  # 12 characters, unguessable


def page_folder(page_id: int) -> Path:
    return UPLOAD_ROOT / "deal_pages" / str(page_id)


def public_base(request) -> str:
    """Where public pages are linked: the deals address if set up, else this site."""
    return config.PUBLIC_URL or config.BASE_URL or str(request.base_url).rstrip("/")


def save_photo(page_id: int, data: bytes) -> Optional[str]:
    """Stores a web-sized JPEG copy: turned upright, at most 1800px, and without
    hidden metadata (phone photos carry GPS). Returns the stored name, or None
    if it isn't an image."""
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        img.thumbnail((PHOTO_MAX_SIDE, PHOTO_MAX_SIDE))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
    except Exception:
        return None
    folder = page_folder(page_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid4().hex}.jpg"
    img.save(folder / name, "JPEG", quality=PHOTO_QUALITY, optimize=True, progressive=True)  # no exif passed = none saved
    return name


def save_contact_photo(page_id: int, data: bytes) -> Optional[str]:
    """A square headshot, 400px, metadata removed."""
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        img = ImageOps.fit(img.convert("RGB"), (400, 400))
    except Exception:
        return None
    folder = page_folder(page_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"contact-{uuid4().hex}.jpg"
    img.save(folder / name, "JPEG", quality=88, optimize=True)
    return name


def is_bot(user_agent: str) -> bool:
    return not user_agent or bool(BOT_PATTERN.search(user_agent))


def device_of(user_agent: str) -> str:
    ua = user_agent.lower()
    if "ipad" in ua or "tablet" in ua:
        return "tablet"
    if "mobile" in ua or "iphone" in ua or "android" in ua:
        return "phone"
    return "computer"


def is_real(view) -> bool:
    """False for automatic link checkers: the page was never on screen. Views from
    before this check existed (on_screen None) count; a checker never opens photos."""
    return view.on_screen is not False or bool(view.photos_seen)


def _visit(person, opens: list, number: int) -> dict:
    """One visit from its opens (oldest first). Its device is the open that looked at the most photos."""
    main = max(reversed(opens), key=lambda v: v.photos_seen)
    return {"person": person, "name": main.link.name if main.link else "General link", "device": main.device,
            "photos": main.photos_seen, "returning": number > 1, "last": opens[-1].created_at}


def view_visits(views) -> list:
    """The real views as visits, newest first."""
    by_person = defaultdict(list)
    for v in sorted((v for v in views if is_real(v)), key=lambda v: v.created_at):
        by_person[v.link_id or v.visitor_id].append(v)
    visits = []
    for person, opens in by_person.items():
        current, number = [opens[0]], 1
        for v in opens[1:]:
            if v.created_at - current[-1].created_at > SAME_VISIT:
                visits.append(_visit(person, current, number))
                current, number = [], number + 1
            current.append(v)
        visits.append(_visit(person, current, number))
    return sorted(visits, key=lambda visit: visit["last"], reverse=True)


def view_people(visits: list) -> list:
    """One row per person, from visits (newest first): latest visit, number of visits, most photos."""
    people = {}
    for visit in visits:
        row = people.setdefault(visit["person"], {"name": visit["name"], "device": visit["device"],
                                                  "last": visit["last"], "visits": 0, "photos": 0})
        row["visits"] += 1
        row["photos"] = max(row["photos"], visit["photos"])
    return list(people.values())


def view_stats(views) -> dict:
    """Totals for a list of views, plus the visits and who came back or opened photos."""
    visits = view_visits(views)
    people = view_people(visits)
    came_back = [p for p in people if p["visits"] > 1]
    opened_photos = [p for p in people if p["photos"]]
    return {
        "views": len(visits),
        "people": len(people),
        "returning": len(came_back),
        "photo_people": len(opened_photos),
        "last": visits[0]["last"] if visits else None,
        "visits": visits,
        "came_back": came_back,
        "opened_photos": opened_photos,
    }


def link_stats(page) -> dict:
    """Per personal link: views, people, last viewed."""
    by_link = defaultdict(list)
    for v in page.views:
        by_link[v.link_id].append(v)
    return {link.id: view_stats(by_link.get(link.id, [])) for link in page.links} | {None: view_stats(by_link.get(None, []))}


def ago(when: Optional[datetime]) -> str:
    if not when:
        return "never"
    seconds = (datetime.utcnow() - when).total_seconds()
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(seconds // 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"

"""
Deal landing pages: a public page per deal (photos, price, facts, contact)
shared by link - one general link plus a personal link per investor, so the
views show who looked.

Visits: an anonymous cookie marks the browser, so a later visit is
"returning". Link-preview fetchers (WhatsApp, iMessage, Facebook...) and
other bots don't count as views.
"""

import io
import re
import secrets
from collections import defaultdict
from datetime import datetime
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


def is_bot(user_agent: str) -> bool:
    return not user_agent or bool(BOT_PATTERN.search(user_agent))


def device_of(user_agent: str) -> str:
    ua = user_agent.lower()
    if "ipad" in ua or "tablet" in ua:
        return "tablet"
    if "mobile" in ua or "iphone" in ua or "android" in ua:
        return "phone"
    return "computer"


def view_stats(views) -> dict:
    """Totals for a list of views (newest first)."""
    visitors = {v.visitor_id for v in views}
    return {
        "views": len(views),
        "people": len(visitors),
        "returning": len({v.visitor_id for v in views if v.is_return}),
        "last": views[0].created_at if views else None,
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

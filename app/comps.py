"""
Tools › Comps: turning a pasted Zillow link into an address, and filling a
house's details from RentCast.

Zillow blocks automated requests (it answers with a CAPTCHA page), so the
system never fetches Zillow. The link is kept so Tal can open it, and the
address inside it is looked up in RentCast instead. Every field stays
editable - RentCast's data can differ slightly from Zillow's.
"""

import re
from datetime import date, datetime
from typing import Optional, Tuple
from urllib.parse import unquote, urlparse

import httpx
from sqlalchemy.orm import Session

from app import config
from app.models import ApiUsage, CompHouse

RENTCAST_URL = "https://api.rentcast.io/v1"
RENTCAST_FREE_CALLS = 50  # Developer plan, per month

STREET_SUFFIXES = {
    "st", "street", "ave", "avenue", "rd", "road", "dr", "drive", "ln", "lane", "ct", "court",
    "blvd", "boulevard", "way", "pl", "place", "ter", "terrace", "cir", "circle", "pkwy",
    "parkway", "hwy", "highway", "trl", "trail", "aly", "alley", "sq", "square", "pike", "run",
    "xing", "crossing", "row", "walk", "ext", "hl", "hill", "hts", "heights",
}
UNIT_WORDS = {"apt", "unit", "ste", "suite", "#", "fl", "floor"}


def parse_input(text: str) -> Tuple[str, Optional[str]]:
    """Returns (address, zillow_url) from a pasted Zillow link or a typed address.

    'https://www.zillow.com/homedetails/344-Long-Rd-Pittsburgh-PA-15235/11451931_zpid/'
    -> ('344 Long Rd, Pittsburgh, PA 15235', <the link>)
    """
    text = (text or "").strip()
    if "zillow.com" not in text.lower():
        return re.sub(r"\s+", " ", text)[:255], None
    url = text if text.startswith("http") else "https://" + text
    path = unquote(urlparse(url).path)
    match = re.search(r"/homedetails/([^/]+)", path)
    if not match:
        return "", url[:500]
    return address_from_slug(match.group(1)), url.split("?")[0][:500]


def split_links(text: str) -> list:
    """Several pasted comps at once: every Zillow link in the text, or - if there
    are none - one address per line."""
    links = re.findall(r"(?:https?://)?(?:www\.)?zillow\.com/homedetails/[^\s,;\"'<>]+", text or "", flags=re.I)
    if links:
        return links
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def address_from_slug(slug: str) -> str:
    tokens = [t for t in slug.split("-") if t]
    # "1319-1-2-North-Ave" is 1319 1/2 North Ave.
    if len(tokens) > 3 and tokens[0].isdigit() and tokens[1] == "1" and tokens[2] in ("2", "4"):
        tokens = [f"{tokens[0]} 1/{tokens[2]}"] + tokens[3:]
    if len(tokens) >= 3 and re.fullmatch(r"\d{5}", tokens[-1]) and re.fullmatch(r"[A-Za-z]{2}", tokens[-2]):
        zip_code, state, rest = tokens[-1], tokens[-2].upper(), tokens[:-2]
        # Street ends after the last street suffix (plus any unit), the rest is the city.
        split = None
        for i, t in enumerate(rest[:-1]):
            if t.lower() in STREET_SUFFIXES:
                split = i + 1
        if split is not None:
            while split < len(rest) - 1 and (rest[split].lower() in UNIT_WORDS or rest[split - 1].lower() in UNIT_WORDS):
                split += 1
            street, city = " ".join(rest[:split]), " ".join(rest[split:])
            return f"{street}, {city}, {state} {zip_code}"
        return f"{' '.join(rest)}, {state} {zip_code}"
    return " ".join(tokens)


def _date(value) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _count_call(db: Session) -> None:
    month = date.today().replace(day=1)
    row = db.query(ApiUsage).filter_by(service="rentcast", month=month).first()
    if not row:
        row = ApiUsage(service="rentcast", month=month, calls=0)
        db.add(row)
    row.calls += 1
    db.flush()  # the session doesn't autoflush - make the next lookup see this row


def calls_this_month(db: Session) -> int:
    row = db.query(ApiUsage).filter_by(service="rentcast", month=date.today().replace(day=1)).first()
    return row.calls if row else 0


def _get(db: Session, path: str, address: str):
    _count_call(db)
    response = httpx.get(
        f"{RENTCAST_URL}{path}",
        params={"address": address, "limit": 5},
        headers={"X-Api-Key": config.RENTCAST_API_KEY, "Accept": "application/json"},
        timeout=20,
    )
    if response.status_code == 404:
        return []
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else [data]


def fill_from_rentcast(db: Session, house: CompHouse, overwrite: bool = False) -> None:
    """Fills empty fields (or all, with overwrite) from RentCast: property
    record (beds, baths, sqft, year, last sale) + the sale listing (listed date)."""
    if not config.RENTCAST_API_KEY:
        house.lookup_note = None  # the page already explains lookups aren't set up
        return
    if not house.address:
        house.lookup_note = "No address to look up."
        return

    found = {}
    try:
        records = _get(db, "/properties", house.address)
        if records:
            r = records[0]
            found.update({
                "bedrooms": r.get("bedrooms"),
                "bathrooms": r.get("bathrooms"),
                "sqft": r.get("squareFootage"),
                "year_built": r.get("yearBuilt"),
                "sold_price": r.get("lastSalePrice"),
                "sold_date": _date(r.get("lastSaleDate")),
            })
            if r.get("formattedAddress"):
                found["address"] = r["formattedAddress"]
        listings = _get(db, "/listings/sale", house.address)
        if listings:
            # The listing that ended in the sale: latest one listed on/before the sale date.
            sold = found.get("sold_date")
            candidates = [l for l in listings if _date(l.get("listedDate"))]
            if sold:
                candidates = [l for l in candidates if _date(l.get("listedDate")) <= sold] or candidates
            if candidates:
                listing = max(candidates, key=lambda l: _date(l.get("listedDate")))
                found["listed_date"] = _date(listing.get("listedDate"))
                if listing.get("daysOnMarket") is not None:
                    found["days_on_market_manual"] = int(listing["daysOnMarket"])
                for key, field in (("bedrooms", "bedrooms"), ("bathrooms", "bathrooms"),
                                   ("squareFootage", "sqft"), ("yearBuilt", "year_built")):
                    if found.get(field) is None and listing.get(key) is not None:
                        found[field] = listing[key]
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        house.lookup_note = ("RentCast rejected the API key." if code in (401, 403)
                             else "RentCast monthly limit reached." if code == 429
                             else f"Lookup failed (RentCast error {code}) - enter the details by hand.")
        return
    except httpx.HTTPError:
        house.lookup_note = "Couldn't reach RentCast - try Refresh later or enter the details by hand."
        return

    if not any(v is not None for k, v in found.items() if k != "address"):
        house.lookup_note = "Not found in RentCast - enter the details by hand."
        return
    for field, value in found.items():
        if value is None:
            continue
        if field == "address":
            if overwrite or not house.address:
                house.address = value[:255]
            continue
        if overwrite or getattr(house, field) is None:
            setattr(house, field, value)
    house.lookup_note = f"Filled from RentCast on {date.today():%b %d, %Y} - check against Zillow."

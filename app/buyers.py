"""
Dispo › Buyers: the investors Tal sells deals to, each with a buybox (zips,
neighborhoods, areas, max price, property types), and which buyers to call
about a deal.

Buyers name places their own way ("North Pittsburgh", "Ross Twp"), so places
are turned into zip codes and matched by distance, not by name. A deal in their
area that also fits their price, bedrooms, grade and type is "Suggested", and so
is a buyer who clicked this deal or a past deal near it (app/buyer_activity.py).
One in their area that misses something, or within NEARBY_MILES of their places,
is "Potential" - with what fits and what doesn't, so Tal decides. Opens are shown
but never bring a buyer in on their own (mail apps count some by themselves). Zip
centre points come from the US Census 2020 zip file (app/data/zip_centroids.csv,
western Pennsylvania only).
"""

import csv
import math
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Set

from sqlalchemy.orm import Session

from app.models import Buyer, DealPage, Neighborhood, WholesaleDeal, ZipCode

NEARBY_MILES = 3
GRADES = ["A", "B", "C", "D"]
PROPERTY_TYPES = ["Single Family", "Multi-Family", "Duplex", "Townhouse", "Condo", "Land"]
CONTACT_CHANNELS = {"email": "Email", "sms": "SMS", "messenger": "Messenger"}

# How buyers' own area names map to SharpREI's regions (agreed with Tal 2026-11-08).
AREA_REGIONS = {
    "north pittsburgh": ["North Hills", "North Side", "North Side Suburbs"],
    "north hills": ["North Hills"],
    "north allegheny": ["North Hills"],
    "north side": ["North Side"],
    "east end": ["East End"],
    "east pittsburgh": ["East End"],
    "city of pittsburgh": ["Downtown", "East End", "North Side", "South Side", "West End"],
    "south hills": ["South Hills", "South Hills Area", "South Side"],
    "south pittsburgh": ["South Hills", "South Hills Area", "South Side"],
    "south hills pittsburgh": ["South Hills", "South Hills Area", "South Side"],
    "west pittsburgh": ["West End", "West Hills", "Airport Corridor", "Char-West"],
    "west allegheny": ["West End", "West Hills", "Airport Corridor", "Char-West"],
    "west allegheny county": ["West End", "West Hills", "Airport Corridor", "Char-West"],
}
ALL_REGIONS = {"greater pittsburgh", "pittsburgh area"}

# Places outside SharpREI's zip list that buyers named - kept so they match if
# Tal ever has a deal there (or nearby).
OUTSIDE_PLACES = {
    "new kensington": ["15068"],
    "murrysville": ["15668"],
    "north huntingdon": ["15642"],
    "peters township": ["15367"],
    "ambridge": ["15003"],
    "washington": ["15301"],
    "beaver county": ["15001", "15003", "15009", "15010", "15061", "15066"],
}

ZIP_AT_END = re.compile(r"\b(\d{5})(?:-\d{4})?\s*$")


def split_list(value: Optional[str]) -> List[str]:
    """ "15202, 15212" -> ["15202", "15212"] (also splits on ; and new lines)."""
    return [p.strip() for p in re.split(r"[,;\n]", value or "") if p.strip()]


def join_list(items) -> Optional[str]:
    items = list(dict.fromkeys(i.strip() for i in items if i and i.strip()))
    return ", ".join(items) or None


@lru_cache(maxsize=1)
def zip_points() -> Dict[str, tuple]:
    path = Path(__file__).resolve().parent / "data" / "zip_centroids.csv"
    with open(path) as f:
        return {row["zip"]: (float(row["lat"]), float(row["lon"])) for row in csv.DictReader(f)}


def miles(a: tuple, b: tuple) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


class Places:
    """SharpREI's zips, regions and neighborhoods, to turn a place name into zips."""

    def __init__(self, db: Session):
        self.region_of: Dict[str, Optional[str]] = {}
        self.region_zips: Dict[str, Set[str]] = defaultdict(set)
        for z in db.query(ZipCode).all():
            region = z.region_override or z.region
            self.region_of[z.zip_code] = region
            if region:
                self.region_zips[region.lower()].add(z.zip_code)
        self.regions = sorted({r for r in self.region_of.values() if r})
        self.neighborhood_zips: Dict[str, Set[str]] = {}
        self.neighborhood_name: Dict[str, str] = {}
        self.zip_neighborhoods: Dict[str, List[str]] = defaultdict(list)
        for n in db.query(Neighborhood).all():
            zips = {link.zip_code.zip_code for link in n.zip_links}
            self.neighborhood_zips[n.name.lower()] = zips
            self.neighborhood_name[n.name.lower()] = n.name
            for z in zips:
                self.zip_neighborhoods[z].append(n.name)

    def neighborhood_zips_for(self, name: str) -> Set[str]:
        key = name.lower()
        return self.neighborhood_zips.get(key) or set(OUTSIDE_PLACES.get(key, []))

    def area_zips(self, name: str) -> Set[str]:
        key = name.lower()
        if key in ALL_REGIONS:
            return set(self.region_of)
        regions = AREA_REGIONS.get(key) or ([name] if key in self.region_zips else [])
        zips = set().union(*(self.region_zips.get(r.lower(), set()) for r in regions)) if regions else set()
        return zips or set(OUTSIDE_PLACES.get(key, []))

    def buyer_places(self, buyer: Buyer) -> List[tuple]:
        """(label, zips) for every place in the buyer's buybox."""
        places = [(z, {z}) for z in split_list(buyer.zip_codes)]
        places += [(n, self.neighborhood_zips_for(n)) for n in split_list(buyer.neighborhoods)]
        places += [(a, self.area_zips(a)) for a in split_list(buyer.areas)]
        return places

    def unknown(self, buyer: Buyer) -> List[str]:
        """Places in the buybox that SharpREI can't place on the map."""
        return [label for label, zips in self.buyer_places(buyer) if not any(z in zip_points() for z in zips)]


def deal_zip(deal: WholesaleDeal) -> Optional[str]:
    if deal.zip_code:
        return deal.zip_code.zip_code
    found = ZIP_AT_END.search(deal.address or "")
    return found.group(1) if found else None


def deal_facts(db: Session, deal: WholesaleDeal, places: Places) -> dict:
    """What a buyer is matched on: where the deal is, its 1st agreement price
    (Tal's choice for now), bedrooms (else the landing page's), grade, and type."""
    zip_code = deal_zip(deal)
    page = db.query(DealPage).filter(DealPage.deal_id == deal.id).first()
    beds = deal.bedrooms if deal.bedrooms is not None else (page.bedrooms if page else None)
    return {
        "zip": zip_code,
        "region": places.region_of.get(zip_code) if zip_code else None,
        "neighborhoods": sorted(places.zip_neighborhoods.get(zip_code, [])) if zip_code else [],
        "price": float(deal.agreement_price) if deal.agreement_price is not None else None,
        "beds": float(beds) if beds is not None else None,
        "grade": deal.neighborhood_grade,
        "type": page.property_type if page and page.property_type else None,
    }


def bed_range(buyer: Buyer) -> Optional[str]:
    lo, hi = buyer.min_beds, buyer.max_beds
    if lo and hi:
        return f"{lo}" if lo == hi else f"{lo}-{hi}"
    return f"{lo}+" if lo else f"up to {hi}" if hi else None


def buybox_checks(buyer: Buyer, facts: dict) -> List[tuple]:
    """(fits, text) for each thing the buyer asked for that the deal has.
    Anything the buyer didn't set, or the deal doesn't have yet, isn't checked."""
    checks = []
    if buyer.max_price is not None and facts["price"] is not None:
        top = float(buyer.max_price)
        checks.append((facts["price"] <= top, f"${facts['price']:,.0f}, they buy up to ${top:,.0f}" if facts["price"] <= top
                       else f"They buy only up to ${top:,.0f} (this one is ${facts['price']:,.0f})"))
    wanted = bed_range(buyer)
    if wanted and facts["beds"] is not None:
        fits = (buyer.min_beds or 0) <= facts["beds"] <= (buyer.max_beds or 99)
        checks.append((fits, f"{facts['beds']:g} beds, they want {wanted}" if fits else f"They want {wanted} beds (this one has {facts['beds']:g})"))
    grades = split_list(buyer.grades)
    if grades and facts["grade"]:
        fits = facts["grade"] in grades
        checks.append((fits, f"{facts['grade']} area, they buy {'/'.join(grades)}" if fits
                       else f"They buy only {'/'.join(grades)} areas (this one is {facts['grade']})"))
    types = split_list(buyer.property_types)
    if types and facts["type"]:
        fits = facts["type"] in types
        checks.append((fits, f"{facts['type']}" if fits else f"They want {', '.join(types)} (this one is {facts['type']})"))
    return checks


def _engagement(act: Optional[dict], deal: WholesaleDeal, here: Optional[tuple]) -> dict:
    """What the buyer did with this deal and with past deals within NEARBY_MILES of it."""
    lines, strong, weak = [], False, False
    if not act:
        return {"lines": lines, "strong": strong, "weak": weak}
    this = act["deals"].get(deal.id)
    if this:
        did = [t for t, yes in (("clicked this deal's email", this["ghl_clicked"]), ("opened its photos", this["sharp_clicked"]),
                                ("opened its email", this["ghl_opened"] and not this["ghl_clicked"]),
                                ("viewed its page", this["sharp_opened"] and not this["sharp_clicked"])) if yes]
        if did:
            lines.append("Already " + ", ".join(did))
            strong = strong or this["ghl_clicked"] or this["sharp_clicked"]
            weak = True
    clicked, opened = [], []
    for key, d in act["deals"].items():
        if key == deal.id or not d["deal"] or not here:
            continue
        z = deal_zip(d["deal"])
        if not z or z not in zip_points() or miles(here, zip_points()[z]) > NEARBY_MILES:
            continue
        street = d["deal"].address.split(",")[0]
        if d["ghl_clicked"] or d["sharp_clicked"]:
            clicked.append(street)
        elif d["ghl_opened"] or d["sharp_opened"]:
            opened.append(street)
    if clicked:
        lines.append(f"Clicked {len(clicked)} past deal{'s' if len(clicked) != 1 else ''} nearby: " + ", ".join(clicked))
        strong = True
    if opened:
        lines.append(f"Opened {len(opened)} past deal{'s' if len(opened) != 1 else ''} nearby: " + ", ".join(opened))
        weak = True
    return {"lines": lines, "strong": strong, "weak": weak}


def suggest_buyers(db: Session, deal: WholesaleDeal) -> dict:
    """"suggested": fits their buybox, or clicked this or nearby past deals - and
    misses nothing checked. "potential": nearby, or misses something. Opens are
    shown as a reason but never bring a buyer in on their own. Each with why."""
    from app.buyer_activity import buyer_activity

    places = Places(db)
    facts = deal_facts(db, deal, places)
    here = zip_points().get(facts["zip"]) if facts["zip"] else None
    activity = buyer_activity(db)
    on_deal_ids = {b.buyer_id for b in deal.buyers if b.buyer_id}
    on_deal_names = {b.name.strip().lower() for b in deal.buyers}
    suggested, potential, no_buybox = [], [], 0
    for buyer in db.query(Buyer).order_by(Buyer.name).all():
        buyer_places = places.buyer_places(buyer)
        act = activity.get(buyer.id)
        engaged = _engagement(act, deal, here)
        # Opens alone don't bring a buyer in (mail apps count some on their own) -
        # they're shown for buyers who are listed for another reason.
        if not buyer_places and not engaged["strong"]:
            no_buybox += 1
            continue
        inside = [label for label, zips in buyer_places if facts["zip"] in zips]
        nearest = None
        if not inside and here and not buyer.exact_area_only:
            for label, zips in buyer_places:
                for z in zips:
                    if z in zip_points():
                        d = miles(here, zip_points()[z])
                        if d <= NEARBY_MILES and (nearest is None or d < nearest[1]):
                            nearest = (label, d)
        if not inside and not nearest and not engaged["strong"]:
            continue
        checks = buybox_checks(buyer, facts)
        if act and act["unsubscribed"]:
            checks.append((False, "Unsubscribed from GHL emails"))
        misses = sum(1 for fits, _ in checks if not fits)
        where = ("In their area: " + ", ".join(inside)) if inside else (f"Nearby: {nearest[1]:.1f} mi from {nearest[0]}" if nearest else None)
        match = {
            "buyer": buyer,
            "where": where,
            "inside": bool(inside),
            "distance": 0 if inside else (nearest[1] if nearest else NEARBY_MILES + 1),
            "checks": checks,
            "misses": misses,
            "engaged": engaged["lines"],
            "strong": engaged["strong"],
            "activity": act,
            "on_deal": buyer.id in on_deal_ids or buyer.name.strip().lower() in on_deal_names,
        }
        (suggested if (inside or engaged["strong"]) and not misses else potential).append(match)
    suggested.sort(key=lambda m: (not m["strong"], not m["inside"], -len(m["checks"]), m["buyer"].name.lower()))
    potential.sort(key=lambda m: (not m["inside"], m["misses"], not m["engaged"], m["distance"], m["buyer"].name.lower()))
    return {"facts": facts, "suggested": suggested, "potential": potential, "no_buybox": no_buybox}

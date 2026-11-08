"""
Dispo › Buyers: the investors Tal sells deals to, each with a buybox (zips,
neighborhoods, areas, max price, property types), and which buyers to call
about a deal.

Buyers name places their own way ("North Pittsburgh", "Ross Twp"), so places
are turned into zip codes and matched by distance, not by name: a deal in one
of their zips is "In their area"; within NEARBY_MILES of one is "Nearby". Zip
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
    """Where the deal is, and its asking price and type from the landing page."""
    zip_code = deal_zip(deal)
    page = db.query(DealPage).filter(DealPage.deal_id == deal.id).first()
    return {
        "zip": zip_code,
        "region": places.region_of.get(zip_code) if zip_code else None,
        "neighborhoods": sorted(places.zip_neighborhoods.get(zip_code, [])) if zip_code else [],
        "price": float(page.asking_price) if page and page.asking_price is not None else None,
        "type": page.property_type if page and page.property_type else None,
    }


def suggest_buyers(db: Session, deal: WholesaleDeal) -> dict:
    """Buyers whose buybox covers the deal ("In their area") or is close to it
    ("Nearby"), with the reasons and anything that doesn't fit (price, type)."""
    places = Places(db)
    facts = deal_facts(db, deal, places)
    here = zip_points().get(facts["zip"]) if facts["zip"] else None
    on_deal_ids = {b.buyer_id for b in deal.buyers if b.buyer_id}
    on_deal_names = {b.name.strip().lower() for b in deal.buyers}
    matches, no_buybox = [], 0
    for buyer in db.query(Buyer).order_by(Buyer.name).all():
        buyer_places = places.buyer_places(buyer)
        if not buyer_places:
            no_buybox += 1
            continue
        inside = [label for label, zips in buyer_places if facts["zip"] in zips]
        nearest = None
        if not inside and here:
            for label, zips in buyer_places:
                for z in zips:
                    if z in zip_points():
                        d = miles(here, zip_points()[z])
                        if d <= NEARBY_MILES and (nearest is None or d < nearest[1]):
                            nearest = (label, d)
        if not inside and not nearest:
            continue
        warnings = []
        if buyer.max_price is not None and facts["price"] is not None and facts["price"] > float(buyer.max_price):
            warnings.append(f"Over their max price (${float(buyer.max_price):,.0f})")
        types = split_list(buyer.property_types)
        if types and facts["type"] and facts["type"] not in types:
            warnings.append("Wants " + ", ".join(types))
        matches.append({
            "buyer": buyer,
            "level": "area" if inside else "nearby",
            "reason": ("In their area: " + ", ".join(inside)) if inside
                      else f"Nearby: {nearest[1]:.1f} mi from {nearest[0]}",
            "distance": 0 if inside else nearest[1],
            "warnings": warnings,
            "on_deal": buyer.id in on_deal_ids or buyer.name.strip().lower() in on_deal_names,
        })
    matches.sort(key=lambda m: (m["level"] != "area", bool(m["warnings"]), m["distance"], m["buyer"].name.lower()))
    return {"facts": facts, "matches": matches, "no_buybox": no_buybox}

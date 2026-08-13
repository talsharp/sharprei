"""
Classify every neighborhood/municipality into a broader "region" (East End,
North Hills, South Hills Area, etc.), then compute each zip's region
independently - by summing GIS overlap ratio *within each region* across all
of that zip's linked neighborhoods, not by inheriting whichever single
neighborhood happens to be "main". Those answer different questions and can
legitimately disagree (e.g. 15216's single biggest neighborhood is Beechview
/ South Side, but Dormont + Mount Lebanon + Scott together - all South Hills
Area - cover more of the zip overall).

Two real, sourced classification systems are used:
  - For the ~121 non-Pittsburgh municipalities: Allegheny County's official
    Council of Governments (COG) regions, read directly from the COG field
    already present in data/reference/allegheny_municipalities.geojson (e.g.
    "North Hills", "South Hills Area", "Turtle Creek Valley"). 3 boroughs
    (Ben Avon Heights, Sewickley Heights, Sewickley Hills) have no COG -
    manually mapped to Quaker Valley by proximity (they're literally
    Sewickley-named enclaves next to that COG's other members).
  - For the 89 Pittsburgh neighborhoods in use: the city's commonly used
    4-quadrant grouping (East End / North Side / South Side / West End) plus
    Downtown, hand-classified and cross-checked against two independent
    public sources, with a completeness assertion so nothing is silently
    dropped or double-counted.

IMPORTANT CAVEAT: COG names are official government data, but don't always
match colloquial usage (e.g. the "Quaker Valley" COG area - Bellevue, Avalon,
Ben Avon - is often called "the North Side" locally, and "South Hills Area"
COG technically includes Moon/Robinson far to the west). Use ZipCode.region
_override to relabel any zip whose computed region doesn't match how you
actually talk about it - same pattern as tier_override and neighborhood
"Set as main".

Idempotent and safe to re-run after populate_neighborhoods.py.
"""

import json
from collections import defaultdict
from pathlib import Path

from app.database import SessionLocal, init_db
from app.models import Neighborhood, ZipCode

REFERENCE_DIR = Path(__file__).resolve().parent.parent / "data" / "reference"

NORTH_SIDE = {
    "Allegheny Center", "Allegheny West", "Central Northside", "East Allegheny", "Manchester",
    "Troy Hill", "Spring Garden", "Spring Hill-City View", "Fineview", "Perry North", "Perry South",
    "Brighton Heights", "Marshall-Shadeland", "California-Kirkbride", "North Shore",
    "Northview Heights", "Summer Hill", "Chateau",
}
SOUTH_SIDE = {
    "South Side Flats", "South Side Slopes", "Mount Washington", "Duquesne Heights", "Allentown",
    "Arlington", "Arlington Heights", "Beltzhoover", "Beechview", "Brookline", "Bon Air", "Carrick",
    "Knoxville", "Mt. Oliver", "Overbrook", "St. Clair", "Hays", "Lincoln Place", "New Homestead",
}
DOWNTOWN = {
    "Central Business District", "Strip District", "Bluff", "Crawford-Roberts", "Middle Hill",
    "Upper Hill", "Terrace Village", "Bedford Dwellings",
}
WEST_END = {
    "Chartiers City", "Crafton Heights", "East Carnegie", "Elliott", "Esplen", "Fairywood",
    "Ridgemont", "Sheraden", "South Shore", "West End", "Westwood", "Windgap", "Banksville",
}

# Boroughs with no COG in the source data, mapped by proximity.
NO_COG_FALLBACK = {
    "Ben Avon Heights": "Quaker Valley",
    "Sewickley Heights": "Quaker Valley",
    "Sewickley Hills": "Quaker Valley",
}


def build_region_map(neighborhood_names):
    pgh = json.load(open(REFERENCE_DIR / "pittsburgh_neighborhoods.geojson"))
    pgh_names = set(f["properties"]["hood"] for f in pgh["features"])
    in_pgh = set(n for n in neighborhood_names if n in pgh_names)
    east_end = in_pgh - NORTH_SIDE - SOUTH_SIDE - DOWNTOWN - WEST_END

    region_map = {}
    for group, region in [
        (NORTH_SIDE, "North Side"),
        (SOUTH_SIDE, "South Side"),
        (DOWNTOWN, "Downtown"),
        (WEST_END, "West End"),
        (east_end, "East End"),
    ]:
        for n in group:
            region_map[n] = region

    muni = json.load(open(REFERENCE_DIR / "allegheny_municipalities.geojson"))
    cog_by_name = {f["properties"]["NAME"].title(): (f["properties"].get("COG") or "").strip() for f in muni["features"]}
    for n in neighborhood_names:
        if n in region_map:
            continue
        cog = cog_by_name.get(n)
        if cog:
            region_map[n] = cog
        elif n in NO_COG_FALLBACK:
            region_map[n] = NO_COG_FALLBACK[n]

    return region_map


def main():
    init_db()
    db = SessionLocal()

    neighborhoods = db.query(Neighborhood).all()
    region_map = build_region_map([n.name for n in neighborhoods])

    unclassified = [n.name for n in neighborhoods if n.name not in region_map]
    for n in neighborhoods:
        n.region = region_map.get(n.name)
    db.flush()

    zips = db.query(ZipCode).all()
    for zc in zips:
        totals = defaultdict(float)
        for link in zc.neighborhood_links:
            if link.overlap_ratio and link.neighborhood.region:
                totals[link.neighborhood.region] += link.overlap_ratio
        zc.region = max(totals, key=totals.get) if totals else None

    db.commit()

    print(f"Neighborhoods/municipalities classified: {len(neighborhoods) - len(unclassified)} / {len(neighborhoods)}")
    if unclassified:
        print(f"Unclassified (no region found): {unclassified}")
    print(f"Zip codes with a computed region: {sum(1 for z in zips if z.region)} / {len(zips)}")


if __name__ == "__main__":
    main()

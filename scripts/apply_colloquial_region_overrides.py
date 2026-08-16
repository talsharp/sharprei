"""
One-off application of region_override values so displayed region names
match what locals actually call these areas, rather than the raw COG
(Council of Governments) administrative names computed by
populate_regions.py. Also splits two COG regions that bundle two
genuinely distinct local identities into one government boundary.

Researched via web search (historical societies, COG membership pages,
school district names, local news outlets) - see conversation for
sourcing per region. Where evidence was weak or the existing computed
name wasn't clearly wrong, the computed region was left untouched.

Idempotent and safe to re-run.
"""

from app.database import SessionLocal, init_db
from app.models import ZipCode

# zip_code -> new region_override value
OVERRIDES = {
    # Allegheny Valley North -> Alle-Kiski Valley (well-documented local term)
    "15014": "Alle-Kiski Valley",
    "15030": "Alle-Kiski Valley",
    "15049": "Alle-Kiski Valley",
    "15065": "Alle-Kiski Valley",
    "15084": "Alle-Kiski Valley",
    "15139": "Alle-Kiski Valley",
    "15144": "Alle-Kiski Valley",

    # Steel Rivers -> Mon Valley (COG's own name is administrative only)
    "15012": "Mon Valley",
    "15018": "Mon Valley",
    "15020": "Mon Valley",
    "15028": "Mon Valley",
    "15034": "Mon Valley",
    "15037": "Mon Valley",
    "15045": "Mon Valley",
    "15047": "Mon Valley",
    "15083": "Mon Valley",
    "15088": "Mon Valley",
    "15110": "Mon Valley",
    "15120": "Mon Valley",
    "15131": "Mon Valley",
    "15132": "Mon Valley",
    "15133": "Mon Valley",
    "15135": "Mon Valley",
    # 15104 (Braddock/Rankin/North Braddock) - computed as Turtle Creek
    # Valley by COG, but these are core Mon River steel towns by identity.
    "15104": "Mon Valley",

    # South Hills Area -> South Hills (true South Hills zips only)
    "15017": "South Hills",
    "15025": "South Hills",
    "15031": "South Hills",
    "15064": "South Hills",
    "15082": "South Hills",
    "15102": "South Hills",
    "15122": "South Hills",
    "15129": "South Hills",
    "15216": "South Hills",
    "15227": "South Hills",
    "15228": "South Hills",
    "15234": "South Hills",
    "15236": "South Hills",
    "15241": "South Hills",
    "15243": "South Hills",

    # Airport Corridor (new) - split off from South Hills Area (Moon/
    # Findlay/Robinson-dominant) and Char-West (North Fayette/South
    # Fayette border zips on the airport side).
    "15108": "Airport Corridor",
    "15231": "Airport Corridor",
    "15126": "Airport Corridor",
    "15275": "Airport Corridor",
    "15205": "Airport Corridor",
    "15276": "Airport Corridor",
    "15057": "Airport Corridor",
    "15071": "Airport Corridor",

    # Quaker Valley - 15202 (Bellevue/Avalon/Ben Avon) split out; 15056/
    # 15143 (Leetsdale/Sewickley) keep the name, it's genuinely local there.
    "15202": "North Side Suburbs",

    # Char-West -> Chartiers Valley (Carnegie/Collier - real school
    # district identity) + West Hills (McKees Rocks/Kennedy/Stowe/
    # Neville/Crescent).
    "15106": "Chartiers Valley",
    "15142": "Chartiers Valley",
    "15046": "West Hills",
    "15136": "West Hills",
    "15225": "West Hills",
}


def main():
    init_db()
    db = SessionLocal()

    zip_rows = {z.zip_code: z for z in db.query(ZipCode).all()}
    updated = 0
    missing = []
    for zip_code, new_region in OVERRIDES.items():
        zc = zip_rows.get(zip_code)
        if zc is None:
            missing.append(zip_code)
            continue
        zc.region_override = new_region
        updated += 1

    db.commit()
    print(f"Applied region_override to {updated} / {len(OVERRIDES)} zip codes")
    if missing:
        print(f"Not found in DB: {missing}")


if __name__ == "__main__":
    main()

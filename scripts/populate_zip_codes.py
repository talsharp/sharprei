"""
Populate ZipCode with every official Allegheny County zip code.

Source: Allegheny County's own GIS portal, mirrored weekly by the Western PA
Regional Data Center (WPRDC) - https://data.wprdc.org/dataset/allegheny-county-zip-code-boundaries2
Local copy: data/reference/allegheny_zip_boundaries.csv

The dataset's `county_name` column is unreliable on its own - many genuine
Allegheny zips (e.g. 15201, 15206, 15218) have it blank. Zips explicitly
tagged with a DIFFERENT county (Butler/Westmoreland/Beaver/Washington) are
real border towns whose primary/official area is in that neighboring county
(verified against independent zip lookups, e.g. 15012 Belle Vernon is
primarily Westmoreland, not Allegheny, despite its polygon touching the
county line). So the rule used here is: keep everything EXCEPT rows with an
explicit non-Allegheny county_name.

Safe to re-run - only inserts zip codes not already present, never touches
or removes existing rows (including ones that turn out not to match, like
15012, which is left alone since it already has real campaign history).
"""

import csv
from pathlib import Path

from app.database import SessionLocal, init_db
from app.models import ZipCode

REFERENCE_CSV = Path(__file__).resolve().parent.parent / "data" / "reference" / "allegheny_zip_boundaries.csv"


def main():
    init_db()
    with open(REFERENCE_CSV) as f:
        rows = list(csv.DictReader(f))

    allegheny_zips = sorted(
        {r["zip"] for r in rows if r["county_name"] in ("", "ALLEGHENY")}
    )
    excluded = sorted(
        (r["zip"], r["name"], r["county_name"]) for r in rows if r["county_name"] not in ("", "ALLEGHENY")
    )

    db = SessionLocal()
    existing = {z.zip_code for z in db.query(ZipCode).all()}
    to_add = [z for z in allegheny_zips if z not in existing]

    for zip_code in to_add:
        db.add(ZipCode(zip_code=zip_code))
    db.commit()

    print(f"Official Allegheny zip codes in source: {len(allegheny_zips)}")
    print(f"Excluded as primarily another county: {len(excluded)}")
    for zip_code, name, county in excluded:
        print(f"  {zip_code} {name} -> {county}")
    print(f"Already in database: {len(existing)}")
    print(f"Newly added: {len(to_add)}")
    print(f"Total zip codes in database now: {db.query(ZipCode).count()}")


if __name__ == "__main__":
    main()

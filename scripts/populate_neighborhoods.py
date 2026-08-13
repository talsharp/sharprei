"""
Populate zip <-> neighborhood links using real GIS boundary overlap, instead
of a hand-typed guess.

Sources (Allegheny County / City of Pittsburgh GIS, via WPRDC):
- data/reference/allegheny_zip_boundaries.geojson  - 109 zip code polygons
- data/reference/pittsburgh_neighborhoods.geojson  - the city's 90 official
  neighborhoods (already one polygon per neighborhood)
- data/reference/allegheny_municipalities.geojson  - all 130 municipalities
  in the county; "PITTSBURGH" (the city itself) is excluded since the
  neighborhoods layer already covers that area at finer granularity - every
  other municipality name (Mt. Lebanon, Bethel Park, ...) stands in for
  "neighborhood" outside city limits, since those areas aren't subdivided
  into official neighborhoods the way Pittsburgh is.

For each zip code, every area (neighborhood or municipality) that covers at
least MIN_OVERLAP_RATIO of the zip's land area is linked. The single area
with the LARGEST overlap is marked as the zip's primary/main neighborhood;
the rest are secondary.

Idempotent and safe to re-run: only touches links this script itself
created (overlap_ratio IS NOT NULL) - any neighborhood you add manually via
the app (which leaves overlap_ratio unset) is left alone.
"""

import json
from pathlib import Path

from shapely.geometry import shape
from shapely.ops import unary_union

from app.database import SessionLocal, init_db
from app.models import Neighborhood, ZipCode, ZipNeighborhood

REFERENCE_DIR = Path(__file__).resolve().parent.parent / "data" / "reference"
MIN_OVERLAP_RATIO = 0.02  # ignore slivers under 2% of the zip's area


def load_zip_polygons():
    data = json.load(open(REFERENCE_DIR / "allegheny_zip_boundaries.geojson"))
    polygons = {}
    for feat in data["features"]:
        zip_code = feat["properties"]["ZIP"]
        polygons[zip_code] = shape(feat["geometry"]).buffer(0)
    return polygons


def load_neighborhood_polygons():
    data = json.load(open(REFERENCE_DIR / "pittsburgh_neighborhoods.geojson"))
    return {feat["properties"]["hood"]: shape(feat["geometry"]).buffer(0) for feat in data["features"]}


def load_municipality_polygons():
    data = json.load(open(REFERENCE_DIR / "allegheny_municipalities.geojson"))
    by_name = {}
    for feat in data["features"]:
        name = feat["properties"]["NAME"].title()
        if name == "Pittsburgh":
            continue  # covered by the neighborhoods layer instead
        by_name.setdefault(name, []).append(shape(feat["geometry"]).buffer(0))
    # A few municipalities are split into multiple polygon parts - merge them.
    return {name: unary_union(parts) for name, parts in by_name.items()}


def main():
    init_db()
    zips = load_zip_polygons()
    neighborhoods = load_neighborhood_polygons()
    municipalities = load_municipality_polygons()

    areas = {}
    collisions = set(neighborhoods) & set(municipalities)
    if collisions:
        print(f"WARNING: name collisions between neighborhoods and municipalities: {collisions}")
    areas.update(municipalities)
    areas.update(neighborhoods)  # neighborhoods win on name collision

    db = SessionLocal()
    neighborhood_cache = {n.name: n for n in db.query(Neighborhood).all()}

    def get_or_create_neighborhood(name: str) -> Neighborhood:
        if name not in neighborhood_cache:
            n = Neighborhood(name=name)
            db.add(n)
            db.flush()
            neighborhood_cache[name] = n
        return neighborhood_cache[name]

    zip_rows = {z.zip_code: z for z in db.query(ZipCode).all()}

    linked_count = 0
    zips_with_no_match = []

    for zip_code, zip_poly in zips.items():
        zc = zip_rows.get(zip_code)
        if zc is None or zip_poly.area == 0:
            continue

        # Clear only the links this script previously created for this zip.
        db.query(ZipNeighborhood).filter(
            ZipNeighborhood.zip_code_id == zc.id,
            ZipNeighborhood.overlap_ratio.isnot(None),
        ).delete()

        matches = []
        for name, area_poly in areas.items():
            if not zip_poly.intersects(area_poly):
                continue
            ratio = zip_poly.intersection(area_poly).area / zip_poly.area
            if ratio >= MIN_OVERLAP_RATIO:
                matches.append((name, ratio))
        matches.sort(key=lambda m: m[1], reverse=True)

        if not matches:
            zips_with_no_match.append(zip_code)
            continue

        for i, (name, ratio) in enumerate(matches):
            neighborhood = get_or_create_neighborhood(name)
            db.add(
                ZipNeighborhood(
                    zip_code_id=zc.id,
                    neighborhood_id=neighborhood.id,
                    is_primary=(i == 0),
                    overlap_ratio=ratio,
                )
            )
            linked_count += 1

    db.commit()

    print(f"Zip codes processed: {len(zips)}")
    print(f"Neighborhood/municipality links created: {linked_count}")
    print(f"Distinct areas used: {len(neighborhood_cache)}")
    if zips_with_no_match:
        print(f"Zips with no matching area (>{MIN_OVERLAP_RATIO*100:.0f}% overlap): {zips_with_no_match}")


if __name__ == "__main__":
    main()

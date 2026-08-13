"""
Update ZipCode.avg_house_value from real, recent sale transactions - not
tax-assessed value (which is frozen to a 2012 base year in Allegheny County
and runs at roughly half of current market value as of 2026).

Source: Allegheny County Department of Administrative Services, Real Estate
Division, via WPRDC's CKAN datastore API - updated monthly.
https://data.wprdc.org/dataset/real-estate-sales

Pulls the current-year and prior-year sale resources LIVE (not a committed
snapshot - the whole point is staying current, so this always fetches fresh),
keeps only:
  - SALECODE "0" (VALID SALE) - excludes gifts, sheriff sales, corporate
    transfers, corrective deeds, etc. which aren't real market transactions
  - sales in the trailing 12 months from today
  - price > 0

Only updates a zip's avg_house_value if it has at least MIN_SALES qualifying
sales in that window - otherwise leaves the existing value untouched rather
than overwriting it with a number based on 1-2 data points.

Safe to re-run any time to refresh (e.g. monthly, matching the source).
"""

import json
import statistics
import urllib.request
from collections import defaultdict
from datetime import date, timedelta

from app.database import SessionLocal, init_db
from app.models import ZipCode

DATASTORE_SEARCH = "https://data.wprdc.org/api/3/action/datastore_search"

# WPRDC resource IDs for the year-by-year sale transaction resources.
# Update this list in January each year to add the new year's resource id
# (found on https://data.wprdc.org/dataset/real-estate-sales) and optionally
# drop resources older than ~2 years back.
RESOURCE_IDS = {
    2025: "57b84145-f9eb-493b-a78f-9bc803413d99",
    2026: "0bd13902-f2ee-4340-b7c8-c54ebd0f6bd0",
}

MIN_SALES = 3
LOOKBACK_DAYS = 365


def fetch_resource(resource_id: str) -> list:
    records = []
    offset = 0
    limit = 5000
    while True:
        url = f"{DATASTORE_SEARCH}?resource_id={resource_id}&limit={limit}&offset={offset}"
        with urllib.request.urlopen(url) as resp:
            payload = json.load(resp)
        batch = payload["result"]["records"]
        records.extend(batch)
        total = payload["result"]["total"]
        if len(batch) < limit or len(records) >= total:
            break
        offset += limit
    return records


def main():
    init_db()
    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()

    all_records = []
    for year, resource_id in RESOURCE_IDS.items():
        records = fetch_resource(resource_id)
        print(f"Fetched {len(records)} records for {year}")
        all_records.extend(records)

    valid = [
        r
        for r in all_records
        if r.get("SALECODE") == "0"
        and r.get("SALEDATE")
        and r["SALEDATE"] >= cutoff
        and r.get("PRICE")
        and r["PRICE"] > 0
        and r.get("PROPERTYZIP")
    ]
    print(f"Valid arm's-length sales in the trailing {LOOKBACK_DAYS} days: {len(valid)} / {len(all_records)}")

    prices_by_zip = defaultdict(list)
    for r in valid:
        prices_by_zip[r["PROPERTYZIP"][:5]].append(r["PRICE"])

    db = SessionLocal()
    zip_rows = {z.zip_code: z for z in db.query(ZipCode).all()}

    updated = 0
    skipped_too_few = 0
    for zip_code, prices in prices_by_zip.items():
        zc = zip_rows.get(zip_code)
        if zc is None:
            continue
        if len(prices) < MIN_SALES:
            skipped_too_few += 1
            continue
        zc.avg_house_value = round(statistics.mean(prices), 2)
        updated += 1

    db.commit()

    print(f"Zip codes updated: {updated}")
    print(f"Zip codes with recent sales but under {MIN_SALES}-sale minimum (left untouched): {skipped_too_few}")


if __name__ == "__main__":
    main()

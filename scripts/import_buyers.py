"""
One-time import of Tal's buyer list (the VA's buyers.xlsx, made from GHL) into
Dispo › Buyers. Fixes place spellings agreed with Tal on 2026-11-08 so they
match SharpREI's neighborhoods and areas.

    python scripts/import_buyers.py uploads/buyers.xlsx            # dry run: shows what would happen
    python scripts/import_buyers.py uploads/buyers.xlsx --apply    # imports

Buyers already on the list (same email, or same name) are skipped, so running
it twice adds nobody twice. Prints counts only - no names or contact details.
"""

import re
import sys
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.buyers import PROPERTY_TYPES, join_list, split_list  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Buyer  # noqa: E402

NEIGHBORHOOD_FIXES = {
    "ross twp": ["Ross"], "ross township": ["Ross"],
    "wilkinsburgh": ["Wilkinsburg"],
    "mt. lebanon": ["Mount Lebanon"], "mt lebanon": ["Mount Lebanon"],
    "mt washington": ["Mount Washington"], "mt. washington": ["Mount Washington"],
    "west miffin": ["West Mifflin"],
    "penn hils": ["Penn Hills"],
    "lawrenceville": ["Lower Lawrenceville", "Central Lawrenceville", "Upper Lawrenceville"],
    "squirrel hill": ["Squirrel Hill North", "Squirrel Hill South"],
    "natrona": ["Harrison"],
}
# Written as neighborhoods, but they're areas.
NEIGHBORHOODS_THAT_ARE_AREAS = {
    "north hills": "North Hills", "north side": "North Side", "south hills": "South Hills",
    "north pittsburgh": "North Pittsburgh", "south pittsburgh": "South Pittsburgh",
    "west pittsburgh": "West Pittsburgh", "south hills pittsburgh": "South Hills",
}
# Too general to match a deal - kept in the notes.
TOO_GENERAL = {"pittsburgh", "pa", "5 miles from pitt"}
CHANNELS = {"sms": "sms", "messenger": "messenger", "email": "email"}


def text(value) -> str:
    return "" if value is None else str(value).strip()


def convert(row: dict) -> tuple:
    """(buyer fields, list of things changed) for one row of the file."""
    changes = []
    neighborhoods, areas, notes = [], split_list(text(row.get("Areas"))), []
    for place in split_list(text(row.get("Neighborhoods"))):
        key = place.lower()
        if key in NEIGHBORHOOD_FIXES:
            neighborhoods += NEIGHBORHOOD_FIXES[key]
            changes.append("spelling")
        elif key in NEIGHBORHOODS_THAT_ARE_AREAS:
            areas.append(NEIGHBORHOODS_THAT_ARE_AREAS[key])
            changes.append("to areas")
        elif key in TOO_GENERAL:
            notes.append(f"Location: {place}")
            changes.append("to notes")
        else:
            neighborhoods.append(place)
    email, phone = text(row.get("Email")), text(row.get("Phone"))
    channels = [CHANNELS[c.lower()] for c in split_list(text(row.get("Channel"))) if c.lower() in CHANNELS]
    if not channels:
        # No email and no phone = Messenger (Tal, 2026-11-08); a phone only = SMS.
        channels = ["email"] if email else ["sms"] if phone else ["messenger"]
        if not email:
            changes.append("sms" if phone else "messenger")
    types = [t for t in PROPERTY_TYPES if t.lower() in {p.lower() for p in split_list(text(row.get("Property types")))}]
    max_price = row.get("Max price")
    try:
        max_price = float(str(max_price).replace(",", "").replace("$", "")) if text(max_price) else None
    except ValueError:
        notes.append(f"Max price: {max_price}")
        max_price = None
    if text(row.get("Notes")):
        notes.insert(0, text(row.get("Notes")))
    return {
        "name": text(row.get("Full Name"))[:120],
        "email": email[:200] or None,
        "phone": phone[:40] or None,
        "channels": ",".join(dict.fromkeys(channels)),
        "zip_codes": join_list(z for z in split_list(text(row.get("Zip codes"))) if re.fullmatch(r"\d{5}", z)),
        "neighborhoods": join_list(neighborhoods),
        "areas": join_list(areas),
        "max_price": max_price,
        "property_types": join_list(types),
        "notes": "\n".join(notes) or None,
        "original_notes": text(row.get("Original notes")) or None,
    }, changes


def main() -> None:
    path, apply = Path(sys.argv[1]), "--apply" in sys.argv
    rows = list(load_workbook(path, data_only=True).worksheets[0].iter_rows(values_only=True))
    header = [text(h) for h in rows[0]]
    db = SessionLocal()
    have_emails = {e.lower() for (e,) in db.query(Buyer.email).filter(Buyer.email.isnot(None))}
    have_names = {n.lower() for (n,) in db.query(Buyer.name)}
    added, skipped, changes = 0, 0, Counter()
    for values in rows[1:]:
        row = dict(zip(header, values))
        if not text(row.get("Full Name")):
            continue
        fields, changed = convert(row)
        if (fields["email"] and fields["email"].lower() in have_emails) or fields["name"].lower() in have_names:
            skipped += 1
            continue
        changes.update(changed)
        db.add(Buyer(**fields))
        have_names.add(fields["name"].lower())
        if fields["email"]:
            have_emails.add(fields["email"].lower())
        added += 1
    print(f"{'Imported' if apply else 'Would import'} {added} buyers, skipped {skipped} already on the list.")
    print("Places fixed:", dict(changes))
    if apply:
        db.commit()
    else:
        db.rollback()


if __name__ == "__main__":
    main()

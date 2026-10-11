"""
Starter rules for recognizing card-statement charges, from Tal's existing
vendors and his answers (2026-10-11). Safe to run more than once: existing
patterns are left alone.

Usage: venv/bin/python scripts/seed_match_rules.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import MatchRule, Vendor  # noqa: E402

VENDOR_RULES = [
    ("HIGHLEVEL", "HighLevel"),
    ("LAUNCH CONTROL", "Launch Control"),
    ("BATCHLEAD", "BatchLeads"),
    ("PAYPAL *VINCENTLOUI", "Vince"),
    ("PAYPAL *KONINGVONIT", "Yvonne"),
    ("OPENAI", "ChatGPT"),
    ("GOOGLE WORKSPACE", "Gsuite"),
    ("FACEBK", "Facebook"),
    ("WEBWORK", "WebWork Tracker"),
    ("ZAPIER", "Zapier"),
    ("HOSTGATOR", "Hostgator"),
    ("MICROSOFT", "Microsoft"),
    ("ANTHROPIC", "Claude"),
    ("דמי כרטיס", "Credit Card Cost"),
]
SKIP_RULES = [
    ("GOOGLE *ADS", "Mom's shop - Google Ads"),
]


def main() -> None:
    db = SessionLocal()
    existing = {r.pattern for r in db.query(MatchRule).all()}
    added = 0
    for pattern, vendor_name in VENDOR_RULES:
        vendor = db.query(Vendor).filter(Vendor.name == vendor_name).first()
        if not vendor:
            print(f"  vendor not found, skipped: {vendor_name}")
            continue
        if pattern not in existing:
            db.add(MatchRule(pattern=pattern, action="vendor", vendor_id=vendor.id))
            added += 1
    for pattern, label in SKIP_RULES:
        if pattern not in existing:
            db.add(MatchRule(pattern=pattern, action="skip", label=label))
            added += 1
    db.commit()
    print(f"added {added} rules, {db.query(MatchRule).count()} total")


if __name__ == "__main__":
    main()

"""
Import a GHL email stats file (Tal's VA's email_history.xlsx) - the same thing
as Dispo › Buyers › GHL email stats › Upload, from the command line. Also links
personal links to buyers whose name matches exactly.

    python scripts/import_email_history.py uploads/email_history.xlsx            # dry run
    python scripts/import_email_history.py uploads/email_history.xlsx --apply

Prints counts only - no names or contact details.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.buyer_activity import import_email_history, link_buyers_by_name  # noqa: E402
from app.buyer_routes import _read_rows  # noqa: E402
from app.database import SessionLocal  # noqa: E402


def main() -> None:
    path, apply = Path(sys.argv[1]), "--apply" in sys.argv
    db = SessionLocal()
    result = import_email_history(db, _read_rows(path.name, path.read_bytes()))
    linked = link_buyers_by_name(db)
    print(f"{'Imported' if apply else 'Would import'}: {result['rows']} rows, {result['new_emails']} emails, "
          f"{result['added']} recipients added, {result['updated']} updated; {result['no_buyer']} addresses not on the buyer list; "
          f"deals not found: {result['no_deal'] or 'none'}; personal links linked by name: {linked}")
    if apply:
        db.commit()
    else:
        db.rollback()


if __name__ == "__main__":
    main()

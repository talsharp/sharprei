"""
One-off migration: add per-category note fields to MonthlyExpense
(utilities_note, insurance_note, repairs_note, management_fees_note,
property_tax_note, other_note) so a specific expense line (e.g. "Repairs
$1,060") can have its own explanation, shown as a hover tooltip - not one
shared note for the whole month.

Idempotent and safe to re-run.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data.db"

NEW_COLUMNS = [
    "utilities_note",
    "insurance_note",
    "repairs_note",
    "management_fees_note",
    "property_tax_note",
    "other_note",
]


def main():
    conn = sqlite3.connect(DB_PATH)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(monthly_expenses)").fetchall()]
    added = 0
    for col in NEW_COLUMNS:
        if col not in cols:
            conn.execute(f"ALTER TABLE monthly_expenses ADD COLUMN {col} VARCHAR(255)")
            added += 1
    conn.commit()
    conn.close()
    print(f"Added {added} / {len(NEW_COLUMNS)} note columns to monthly_expenses")


if __name__ == "__main__":
    main()

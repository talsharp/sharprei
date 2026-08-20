"""
One-off migration: remove Property.zip_code_id (SQLite can't drop a column
that's part of a foreign key without rebuilding the table), add
tenant_move_in_date and account_balance to Property, and add contractor to
RenovationExpense.

Idempotent and safe to re-run.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data.db"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys=OFF")

    prop_cols = [r[1] for r in conn.execute("PRAGMA table_info(properties)").fetchall()]
    if "zip_code_id" in prop_cols:
        conn.execute(
            """
            CREATE TABLE properties_new (
                id INTEGER NOT NULL,
                address VARCHAR(255) NOT NULL,
                purchase_price NUMERIC(12, 2) NOT NULL,
                closing_costs NUMERIC(12, 2) NOT NULL,
                purchase_date DATE,
                max_arv NUMERIC(12, 2),
                estimated_value NUMERIC(12, 2),
                rent_price NUMERIC(10, 2),
                tenant_move_in_date DATE,
                account_balance NUMERIC(12, 2),
                notes TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                PRIMARY KEY (id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO properties_new (id, address, purchase_price, closing_costs, purchase_date, max_arv, estimated_value, rent_price, notes, created_at, updated_at)
            SELECT id, address, purchase_price, closing_costs, purchase_date, max_arv, estimated_value, rent_price, notes, created_at, updated_at
            FROM properties
            """
        )
        old_count = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
        new_count = conn.execute("SELECT COUNT(*) FROM properties_new").fetchone()[0]
        assert old_count == new_count, f"row count mismatch: {old_count} vs {new_count}, aborting"
        conn.execute("DROP TABLE properties")
        conn.execute("ALTER TABLE properties_new RENAME TO properties")
        print(f"Rebuilt properties table without zip_code_id ({old_count} rows preserved)")
    else:
        # Table already rebuilt - just add any missing new columns.
        if "tenant_move_in_date" not in prop_cols:
            conn.execute("ALTER TABLE properties ADD COLUMN tenant_move_in_date DATE")
            print("Added tenant_move_in_date")
        if "account_balance" not in prop_cols:
            conn.execute("ALTER TABLE properties ADD COLUMN account_balance NUMERIC(12,2)")
            print("Added account_balance")

    reno_cols = [r[1] for r in conn.execute("PRAGMA table_info(renovation_expenses)").fetchall()]
    if "contractor" not in reno_cols:
        conn.execute("ALTER TABLE renovation_expenses ADD COLUMN contractor VARCHAR(150)")
        print("Added contractor to renovation_expenses")

    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()

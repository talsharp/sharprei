"""
One-off migration: create property_owners table (per-property owner name +
ownership percentage, used on the investor report).

Idempotent and safe to re-run.
"""

from app.database import SessionLocal, init_db


def main():
    # PropertyOwner is a brand-new table declared in app/models.py -
    # init_db()'s create_all() creates it automatically if missing.
    init_db()
    print("property_owners table created (or already existed).")


if __name__ == "__main__":
    main()

"""
One-off migration: create the properties / renovation_expenses /
monthly_expenses tables for the Rentals module. Uses raw SQL matching this
app's no-Alembic convention (SQLAlchemy's create_all handles new tables
automatically via init_db(), so this script mainly documents the schema and
is safe to re-run - it only creates tables that don't already exist).
"""

from app.database import SessionLocal, init_db


def main():
    # init_db() calls Base.metadata.create_all(), which creates any table
    # declared in app/models.py that doesn't already exist in the DB - so
    # the new Property/RenovationExpense/MonthlyExpense tables just need
    # the app restarted, or this run once locally.
    init_db()
    print("Rentals tables created (or already existed).")


if __name__ == "__main__":
    main()

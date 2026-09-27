"""
Runs before the web server starts (locally optional, on Render every boot):
brings the database structure up to date with Alembic.

- Brand-new empty database: create all tables and mark them as current.
- Database from before Alembic existed: mark it as the baseline, then upgrade.
- Otherwise: apply any pending migrations.
"""

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import models  # noqa: E402,F401
from app.database import Base, engine  # noqa: E402

BASELINE = "01a81e5ba8d0"


def main() -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    tables = set(inspect(engine).get_table_names())

    if "alembic_version" not in tables:
        if not tables:
            print("prestart: empty database - creating tables")
            Base.metadata.create_all(engine)
            command.stamp(cfg, "head")
            return
        print("prestart: database predates migrations - marking baseline")
        command.stamp(cfg, BASELINE)

    command.upgrade(cfg, "head")
    Base.metadata.create_all(engine)  # tables added since the baseline that no migration creates
    print("prestart: database is up to date")


if __name__ == "__main__":
    main()

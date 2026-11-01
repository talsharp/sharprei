"""
Runs before the web server starts (locally optional, on Render every boot):
brings the database structure up to date with Alembic.

- Brand-new empty database: create all tables and mark them as current.
- Database from before Alembic existed: mark it as the baseline, then upgrade.
- Otherwise: apply any pending migrations.

On Render it then starts the web server itself (SERVER_ARGS), so the server's
settings live here in the code - Render's start command is set in its
dashboard and doesn't follow render.yaml.
"""

import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import models  # noqa: E402,F401
from app.database import Base, backup_db, engine  # noqa: E402

BASELINE = "01a81e5ba8d0"
# Keep-alive above Render's proxy (uvicorn's default is 5s): otherwise the proxy
# sometimes reuses a connection the app just closed and the visitor gets a 502.
SERVER_ARGS = ["--host", "0.0.0.0", "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-keep-alive", "120"]


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

    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    head = ScriptDirectory.from_config(cfg).get_current_head()
    if current != head:
        print(f"prestart: backing up before migrating {current} -> {head}: {backup_db('before_migration').name}")

    command.upgrade(cfg, "head")
    Base.metadata.create_all(engine)  # tables added since the baseline that no migration creates
    print("prestart: database is up to date")


def serve() -> None:
    """Replaces this process with the web server (the start command's own
    `&& uvicorn ...` never runs)."""
    uvicorn = str(Path(sys.executable).parent / "uvicorn")
    os.execv(uvicorn, [uvicorn, "app.main:app", "--port", os.environ.get("PORT", "10000"), *SERVER_ARGS])


if __name__ == "__main__":
    main()
    if os.environ.get("RENDER"):
        sys.stdout.flush()
        serve()

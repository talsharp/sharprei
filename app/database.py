import os
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("DB_PATH", ROOT / "data.db"))
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", ROOT / "backups"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
BACKUPS_TO_KEEP = 45


def backup_db(label: str) -> Path:
    """Consistent copy of the database into backups/ (safe while the app is
    running, unlike copying the file), checked for corruption before it's
    kept. Used nightly and before risky bulk writes like the weekly import."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"data_{timestamp}_{label}.db"
    src = sqlite3.connect(DB_PATH)
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
        if out.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError(f"Backup {dest.name} failed its integrity check")
    finally:
        out.close()
        src.close()

    backups = sorted(BACKUP_DIR.glob("data_*.db"), key=lambda p: p.stat().st_mtime)
    for old in backups[:-BACKUPS_TO_KEEP]:
        old.unlink()

    return dest


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app import models  # noqa: F401  (ensure models are registered)

    Base.metadata.create_all(bind=engine)

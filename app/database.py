import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DB_PATH = Path(__file__).resolve().parent.parent / "data.db"
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups"
BACKUP_DIR.mkdir(exist_ok=True)
BACKUPS_TO_KEEP = 20


def backup_db(label: str) -> Path:
    """Copies data.db to backups/ before a risky bulk write (e.g. the weekly
    import commit), so a bad upload can be undone by restoring the file.
    Keeps only the most recent BACKUPS_TO_KEEP backups."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"data_{timestamp}_{label}.db"
    shutil.copy2(DB_PATH, dest)

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

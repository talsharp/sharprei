"""
Nightly backup + restore test.

1. Takes a consistent backup of the database (app.database.backup_db).
2. Restores it into a temporary file and opens it like the app would.
3. Checks every table has the same row count as the live database, and that
   the finance totals and zip-code metrics computed from the restored copy
   match the live ones.

Exits non-zero (and shows a macOS notification when run on a Mac) if anything
doesn't match, so a broken backup is noticed the same night, not months later.

Usage: venv/bin/python scripts/backup_and_verify.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import BACKUP_DIR, UPLOAD_ROOT, SessionLocal, backup_db, engine  # noqa: E402
from app.finance import build_month_summaries, build_totals  # noqa: E402
from app.metrics import get_zip_metrics  # noqa: E402


def fingerprint(eng, session_factory) -> dict:
    counts = {}
    with eng.connect() as conn:
        for table in sorted(inspect(eng).get_table_names()):
            counts[table] = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()
    db = session_factory()
    try:
        totals = build_totals(db, build_month_summaries(db))
        metrics = get_zip_metrics(db)
        key_figures = {
            "wholesale_expenses": round(totals.wholesale_expenses, 2),
            "revenue": round(totals.revenue, 2),
            "leads": totals.leads,
            "zip_codes": len(metrics),
            "total_sms": sum(m.total_sms for m in metrics),
        }
    finally:
        db.close()
    return {"counts": counts, "figures": key_figures}


STATUS_FILE = BACKUP_DIR / "last_backup.json"
FILE_FOLDERS = ["deals", "properties", "renovations"]
FILE_MIRROR = BACKUP_DIR / "files"


def _live_files():
    for folder in FILE_FOLDERS:
        root = UPLOAD_ROOT / folder
        if root.exists():
            yield from (p for p in root.rglob("*") if p.is_file())


def mirror_files() -> int:
    """Copies uploaded files (deal documents, receipts) into the backup folder.
    Never deletes from the mirror, so a file deleted in the app is recoverable."""
    copied = 0
    for src in _live_files():
        dest = FILE_MIRROR / src.relative_to(UPLOAD_ROOT)
        if not dest.exists() or dest.stat().st_size != src.stat().st_size:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            copied += 1
    return copied


def verify_files() -> list:
    problems = []
    for src in _live_files():
        dest = FILE_MIRROR / src.relative_to(UPLOAD_ROOT)
        if not dest.exists() or dest.stat().st_size != src.stat().st_size:
            problems.append(f"file not backed up: {src.relative_to(UPLOAD_ROOT)}")
    return problems


def write_status(ok: bool, message: str) -> None:
    STATUS_FILE.write_text(json.dumps({"ok": ok, "at": datetime.utcnow().isoformat(), "message": message}))


def notify(message: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e", f'display notification "{message}" with title "SharpREI backup"'], check=False)


def main(backup_override=None) -> int:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    backup = backup_override or backup_db("nightly")
    copied_files = mirror_files() if backup_override is None else 0

    live = fingerprint(engine, SessionLocal)

    with tempfile.TemporaryDirectory() as tmp:
        restored_path = Path(tmp) / "restored.db"
        shutil.copy2(backup, restored_path)
        restored_engine = create_engine(f"sqlite:///{restored_path}")
        try:
            restored = fingerprint(restored_engine, sessionmaker(bind=restored_engine))
        except Exception as exc:
            restored = {"counts": {}, "figures": {}, "error": f"restored copy couldn't be read: {exc.__class__.__name__}: {str(exc).splitlines()[0]}"}
        finally:
            restored_engine.dispose()

    problems = [restored["error"]] if restored.get("error") else []
    for table, n in live["counts"].items():
        if restored["counts"].get(table) != n:
            problems.append(f"{table}: live {n} rows, restored {restored['counts'].get(table)}")
    for key, value in live["figures"].items():
        if restored["figures"].get(key) != value:
            problems.append(f"{key}: live {value}, restored {restored['figures'].get(key)}")

    if backup_override is None:
        problems += verify_files()

    if problems:
        print(f"[{stamp}] BACKUP VERIFY FAILED for {backup.name}:")
        for p in problems:
            print("  -", p)
        notify("Backup restore test FAILED - check logs/backup.log")
        if backup_override is None:
            write_status(False, "; ".join(problems)[:500])
        return 1

    tables = len(live["counts"])
    rows = sum(live["counts"].values())
    files = sum(1 for _ in _live_files())
    print(f"[{stamp}] OK {backup.name}: restored and verified {tables} tables, {rows} rows, figures {restored['figures']}; files {files} backed up ({copied_files} new)")
    if backup_override is None:
        write_status(True, backup.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""On the server there's no Mac to run the nightly backup, so the app runs it
itself once a day (BACKUP_HOUR_UTC, default 07:00 UTC = 3am Eastern)."""

import threading
import time
import traceback
from datetime import datetime, timedelta

from app import config


def _seconds_until_next_run() -> float:
    now = datetime.utcnow()
    run = now.replace(hour=config.BACKUP_HOUR_UTC, minute=0, second=0, microsecond=0)
    if run <= now:
        run += timedelta(days=1)
    return (run - now).total_seconds()


def _loop() -> None:
    from scripts import backup_and_verify

    while True:
        time.sleep(_seconds_until_next_run())
        try:
            backup_and_verify.main()
        except Exception:
            traceback.print_exc()
            try:
                backup_and_verify.write_status(False, "backup crashed - see server logs")
            except Exception:
                traceback.print_exc()


def start() -> None:
    if config.NIGHTLY_BACKUP:
        threading.Thread(target=_loop, name="nightly-backup", daemon=True).start()

"""
Keeps an eye on the server (Tal, 2026-11-01: investors must never hit a broken
page, and if something goes wrong he wants to know).

- Every error on a page, every time the server gets stuck, and every restart
  that wasn't a normal deploy is saved as a ServerProblem and shows in the bell.
- The same goes to a log file on the disk (logs/server.log next to the
  database), so a problem can still be traced days later - Render keeps no
  log file of its own on the server.
- /health/public checks what investors see: a published deal page renders.

Problems that happen while the server is completely down (it can't record
those itself) are caught by the outside uptime monitor watching /health/public.
"""

import asyncio
import logging
import signal
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool

from app.database import DB_PATH, SessionLocal
from app.models import ServerProblem

LOG_DIR = DB_PATH.parent / "logs"
RUNNING_MARK = LOG_DIR / "server.running"
STUCK_SECONDS = 10      # the server not answering for this long counts as stuck
SLOW_SECONDS = 8        # a page taking this long goes to the log file
REPEAT_QUIET = timedelta(minutes=10)  # the same problem isn't recorded again within this

log = logging.getLogger("sharprei")


def _setup_log_file() -> None:
    if log.handlers:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_DIR / "server.log", maxBytes=1_000_000, backupCount=5)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.addHandler(logging.StreamHandler(sys.stderr))
    log.setLevel(logging.INFO)


def record(kind: str, message: str, detail: str = None) -> None:
    """Saves a problem for the bell (and the log file). Never raises."""
    log.error("%s: %s%s", kind, message, ("\n" + detail) if detail else "")
    db = SessionLocal()
    try:
        recent = (db.query(ServerProblem.id)
                  .filter(ServerProblem.kind == kind, ServerProblem.message == message[:255],
                          ServerProblem.created_at > datetime.utcnow() - REPEAT_QUIET)
                  .first())
        if not recent:
            db.add(ServerProblem(kind=kind, message=message[:255], detail=detail))
            db.commit()
    except Exception:
        log.exception("could not save the problem")
    finally:
        db.close()


def _watch_stuck(loop: asyncio.AbstractEventLoop, loop_thread: int) -> None:
    """Every couple of seconds, checks the server can still take requests. If
    it's stuck, notes where in the code it is stuck, for the fix."""
    while True:
        answered = threading.Event()
        started = time.monotonic()
        try:
            loop.call_soon_threadsafe(answered.set)
        except RuntimeError:
            return  # the server is shutting down
        if not answered.wait(STUCK_SECONDS):
            frame = sys._current_frames().get(loop_thread)
            where = "".join(traceback.format_stack(frame)[-12:]) if frame else ""
            answered.wait()
            seconds = time.monotonic() - started
            record("stuck", f"The server was stuck for {seconds:.0f} seconds - pages didn't load meanwhile", where)
        time.sleep(2)


def _note_stop_request() -> None:
    """A stop asked for by Render (deploy, restart) clears the mark right away -
    uvicorn may still be closing connections when Render ends the process, and
    that mustn't look like a crash."""
    try:
        uvicorn_handler = signal.getsignal(signal.SIGTERM)
        if not callable(uvicorn_handler):
            return

        def handler(sig, frame):
            RUNNING_MARK.unlink(missing_ok=True)
            log.info("stop requested (deploy or restart)")
            uvicorn_handler(sig, frame)

        signal.signal(signal.SIGTERM, handler)
    except ValueError:
        pass  # not the main thread (tests)


def on_startup() -> None:
    _setup_log_file()
    # The mark is removed on a normal stop (deploy or restart from Render).
    # Still there at startup = the last run ended without stopping properly.
    if RUNNING_MARK.exists():
        since = datetime.utcfromtimestamp(RUNNING_MARK.stat().st_mtime).strftime("%b %d %H:%M UTC")
        record("restart", "The server restarted unexpectedly (crash or out of memory)",
               f"It had been running since {since}.")
    RUNNING_MARK.write_text(datetime.utcnow().isoformat())
    _note_stop_request()
    log.info("server started")
    loop = asyncio.get_running_loop()
    threading.Thread(target=_watch_stuck, args=(loop, threading.get_ident()), name="stuck-watch", daemon=True).start()


def on_shutdown() -> None:
    RUNNING_MARK.unlink(missing_ok=True)
    log.info("server stopped normally")


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def watch_requests(request: Request, call_next):
        started = time.monotonic()
        where = f"{request.method} {request.url.netloc}{request.url.path}"
        try:
            response = await call_next(request)
        except Exception:
            await run_in_threadpool(record, "error", f"Error on {where}", traceback.format_exc()[-3000:])
            raise
        seconds = time.monotonic() - started
        if response.status_code >= 500 and request.url.path != "/health/public":
            await run_in_threadpool(record, "error", f"{where} answered {response.status_code}")
        elif seconds > SLOW_SECONDS:
            log.warning("slow: %s took %.1fs", where, seconds)
        return response

    app.add_event_handler("startup", on_startup)
    app.add_event_handler("shutdown", on_shutdown)


def open_problems(db) -> list:
    return db.query(ServerProblem).filter(ServerProblem.seen.is_(False)).order_by(ServerProblem.id.desc()).limit(10).all()

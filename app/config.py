import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SECRET_KEY = os.environ.get("SECRET_KEY", "")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
OWNER_EMAILS = {e.strip().lower() for e in os.environ.get("OWNER_EMAIL", "").split(",") if e.strip()}
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
# Public pages (deal landing pages /d/, comp shares /c/) are linked on this
# address when set, e.g. https://deals.sharprei.com - so investors never see
# the app's address. That host serves only those pages.
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
PUBLIC_HOST = urlparse(PUBLIC_URL).netloc if PUBLIC_URL else ""
# Property data lookups for Tools › Comps (rentcast.io). Without it, comps are entered by hand.
RENTCAST_API_KEY = os.environ.get("RENTCAST_API_KEY", "").strip()

if len(SECRET_KEY) < 32:
    raise RuntimeError("SECRET_KEY is missing or too short - set it in .env (or the host's environment settings).")

GOOGLE_CONFIGURED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
BASE_HOST = urlparse(BASE_URL).netloc if BASE_URL else ""
ON_RENDER = bool(os.environ.get("RENDER"))
SECURE_COOKIES = BASE_URL.startswith("https://") or ON_RENDER
NIGHTLY_BACKUP = os.environ.get("NIGHTLY_BACKUP") == "1"
BACKUP_HOUR_UTC = int(os.environ.get("BACKUP_HOUR_UTC", "7"))

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

if len(SECRET_KEY) < 32:
    raise RuntimeError("SECRET_KEY is missing or too short - set it in .env (or the host's environment settings).")

GOOGLE_CONFIGURED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
BASE_HOST = urlparse(BASE_URL).netloc if BASE_URL else ""
SECURE_COOKIES = BASE_URL.startswith("https://")

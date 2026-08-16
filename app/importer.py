import re
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

TARGET_FIELDS = {
    "zip_code": ["zip", "zip_code", "zipcode", "zip code", "קוד מיקוד", "זיפ קוד", "מיקוד"],
    "run_date": ["date", "run_date", "run date", "תאריך"],
    "run_type": ["type", "run_type", "run type", "סוג", "סוג הרצה"],
    "sms_sent": ["sms", "sms_sent", "sms sent", "total messages", "total sent", "כמות סמסים", "סמסים", "נשלחו"],
    "replies": ["replies", "reply", "total response", "response", "תגובות"],
    "leads": ["leads", "lead", "hot lead", "hot", "לידים"],
    "warm": ["warm", "חם"],
    "drip": ["drip", "drips", "follow up", "follow-up", "דריפ"],
    "signed_agreements": ["signed agreements", "signed agreement", "agreements signed", "contracts signed"],
    "opt_out": ["opt_out", "opt-out", "optout", "opt out", "dnc", "הסרה"],
}

FIELD_LABELS = {
    "zip_code": "Zip Code",
    "run_date": "Date",
    "run_type": "Run Type",
    "sms_sent": "SMS Sent",
    "replies": "Replies",
    "leads": "Leads",
    "warm": "Warm",
    "drip": "Drip",
    "signed_agreements": "Signed Agreements",
    "opt_out": "Opt-out",
}


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(s).strip().lower())


def save_upload(filename: str, content: bytes) -> str:
    token = f"{uuid.uuid4().hex}_{filename}"
    (UPLOAD_DIR / token).write_bytes(content)
    return token


def read_table(token: str) -> pd.DataFrame:
    path = UPLOAD_DIR / token
    if path.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(path, dtype=str)
    return pd.read_csv(path, dtype=str)


def guess_mapping(columns: List[str]) -> Dict[str, Optional[str]]:
    normalized = {col: _norm(col) for col in columns}
    mapping: Dict[str, Optional[str]] = {}
    for field, aliases in TARGET_FIELDS.items():
        norm_aliases = [_norm(a) for a in aliases]
        match = next((col for col, n in normalized.items() if n in norm_aliases), None)
        mapping[field] = match
    return mapping


def clean_zip(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    match = re.search(r"\d{5}", text)
    return match.group(0) if match else None


def parse_int(value) -> int:
    if value is None:
        return 0
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return 0
    text = re.sub(r"[^\d\-]", "", text)
    return int(text) if text else 0


def parse_optional_int(value) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return parse_int(text)


def classify_run_type(value, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text or text == "nan":
        return default
    if "follow" in text or "מעקב" in text or "שני" in text:
        return "follow_up"
    return "initial"

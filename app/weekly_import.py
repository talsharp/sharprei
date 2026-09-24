"""
Weekly VA Excel upload: a 4-tab workbook (First Text, Follow up 1, Follow up
2, Follow up 3), each with Zip Code / SMS Sent / Replies / Hot Leads / Warm
Leads / Drips. Each week's numbers are treated as the current total-to-date
for that zip's round (confirmed with the user), so this UPSERTS - it
overwrites the matching CampaignRun's metrics rather than adding a new row
every week, and creates the round if it doesn't exist yet.

A zip's follow-up "round number" isn't a stored column anywhere else in the
app (see app/followups.py) - it's purely "the Nth follow-up CampaignRun for
that zip, in (run_date, id) order". "Follow up 2" in the workbook means "the
2nd such row for that zip", matching that same implicit ordering.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

import pandas as pd
from sqlalchemy.orm import Session

from app.models import CampaignRun, RunType, ZipCode, ZipStatus

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

SHEET_ALIASES = {
    "initial": ["first text", "firsttext", "initial", "initial text", "text1", "text 1"],
    "follow_up_1": ["follow up 1", "followup 1", "followup1", "follow-up 1", "fu1", "f1"],
    "follow_up_2": ["follow up 2", "followup 2", "followup2", "follow-up 2", "fu2", "f2"],
    "follow_up_3": ["follow up 3", "followup 3", "followup3", "follow-up 3", "fu3", "f3"],
}

ROUND_ORDER = ["initial", "follow_up_1", "follow_up_2", "follow_up_3"]
ROUND_LABELS = {
    "initial": "First Text",
    "follow_up_1": "Follow up 1",
    "follow_up_2": "Follow up 2",
    "follow_up_3": "Follow up 3",
}

COLUMN_ALIASES = {
    "zip_code": ["zip", "zip_code", "zipcode", "zip code"],
    "sms_sent": ["sms", "sms_sent", "sms sent", "total messages", "total sent", "messages sent"],
    "replies": ["replies", "reply", "total response", "response", "total replies"],
    "leads": ["leads", "lead", "hot lead", "hot leads", "hot"],
    "warm": ["warm", "warm lead", "warm leads"],
    "drip": ["drip", "drips"],
}

METRIC_FIELDS = ["sms_sent", "replies", "leads", "warm", "drip"]
METRIC_LABELS = {
    "sms_sent": "SMS Sent",
    "replies": "Replies",
    "leads": "Hot Leads",
    "warm": "Warm Leads",
    "drip": "Drips",
}


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(s).strip().lower())


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


def save_workbook(filename: str, content: bytes) -> str:
    token = f"{uuid4().hex}_{filename}"
    (UPLOAD_DIR / token).write_bytes(content)
    return token


def read_workbook(token: str) -> Dict[str, pd.DataFrame]:
    """Returns {round_key: DataFrame} for whichever of the 4 expected tabs
    could be matched by name - missing/unrecognized tabs are just absent."""
    path = UPLOAD_DIR / token
    all_sheets = pd.read_excel(path, sheet_name=None, dtype=str)
    normalized = {name: _norm(name) for name in all_sheets}
    result = {}
    for round_key, aliases in SHEET_ALIASES.items():
        norm_aliases = [_norm(a) for a in aliases]
        match = next((name for name, n in normalized.items() if n in norm_aliases), None)
        if match:
            result[round_key] = all_sheets[match]
    return result


def _match_columns(columns: List[str]) -> Dict[str, Optional[str]]:
    normalized = {col: _norm(str(col)) for col in columns}
    mapping = {}
    for field_name, aliases in COLUMN_ALIASES.items():
        norm_aliases = [_norm(a) for a in aliases]
        match = next((col for col, n in normalized.items() if n in norm_aliases), None)
        mapping[field_name] = match
    return mapping


@dataclass
class RowChange:
    round_key: str
    zip_code: str
    action: str  # "update", "create", "skip"
    new_values: Dict[str, int] = field(default_factory=dict)
    old_values: Optional[Dict[str, int]] = None
    zip_is_new: bool = False
    campaign_run_id: Optional[int] = None
    warning: Optional[str] = None

    @property
    def round_label(self) -> str:
        return ROUND_LABELS[self.round_key]

    @property
    def has_changes(self) -> bool:
        if self.action != "update" or self.old_values is None:
            return True
        return any(self.old_values.get(f) != self.new_values.get(f) for f in METRIC_FIELDS)


def build_plan(db: Session, sheets: Dict[str, pd.DataFrame]) -> List[RowChange]:
    """Pure read-only diff: what WOULD happen, without writing anything.
    Called once for the preview, then again (via apply_plan) at commit time
    against a possibly-changed DB state, matching this app's existing
    token-based re-read-on-commit pattern rather than serializing a plan.

    Rounds are processed in order (initial, then follow-up 1/2/3) and a
    zip's planned follow-up count is tracked as we go - not just read from
    the DB - so a zip appearing in both "Follow up 1" and "Follow up 2" in
    the SAME upload (the normal case: this week's file usually includes
    every round that's happened so far, not just the newest one) correctly
    creates both rather than skipping round 2 as "round 1 doesn't exist yet"."""
    changes: List[RowChange] = []
    # zip_code -> planned follow-up round count so far in this pass, seeded
    # from actual DB rows on first use and incremented as rounds are planned.
    planned_follow_up_count: Dict[str, int] = {}

    def follow_up_count(zc: Optional[ZipCode], zip_val: str) -> int:
        if zip_val not in planned_follow_up_count:
            count = 0
            if zc:
                count = (
                    db.query(CampaignRun)
                    .filter(CampaignRun.zip_code_id == zc.id, CampaignRun.run_type == RunType.follow_up)
                    .count()
                )
            planned_follow_up_count[zip_val] = count
        return planned_follow_up_count[zip_val]

    for round_key in ROUND_ORDER:
        df = sheets.get(round_key)
        if df is None:
            continue
        col_map = _match_columns(list(df.columns))
        if not col_map.get("zip_code"):
            changes.append(
                RowChange(
                    round_key=round_key,
                    zip_code="",
                    action="skip",
                    warning=f'Could not find a Zip Code column in the "{ROUND_LABELS[round_key]}" tab - skipped the whole sheet.',
                )
            )
            continue

        for _, row in df.iterrows():
            zip_val = clean_zip(row.get(col_map["zip_code"]))
            if not zip_val:
                continue  # blank / summary / total row - not an error, just not a zip row

            new_values = {f: parse_int(row.get(col_map.get(f))) for f in METRIC_FIELDS}
            zc = db.query(ZipCode).filter(ZipCode.zip_code == zip_val).first()
            zip_is_new = zc is None

            if round_key == "initial":
                existing = None
                if zc:
                    existing = (
                        db.query(CampaignRun)
                        .filter(CampaignRun.zip_code_id == zc.id, CampaignRun.run_type == RunType.initial)
                        .order_by(CampaignRun.id)
                        .first()
                    )
                if existing:
                    changes.append(
                        RowChange(
                            round_key=round_key,
                            zip_code=zip_val,
                            action="update",
                            new_values=new_values,
                            old_values={f: getattr(existing, f) for f in METRIC_FIELDS},
                            campaign_run_id=existing.id,
                        )
                    )
                else:
                    changes.append(
                        RowChange(round_key=round_key, zip_code=zip_val, action="create", new_values=new_values, zip_is_new=zip_is_new)
                    )
            else:
                round_num = int(round_key[-1])
                db_count = 0
                existing_runs = []
                if zc:
                    existing_runs = (
                        db.query(CampaignRun)
                        .filter(CampaignRun.zip_code_id == zc.id, CampaignRun.run_type == RunType.follow_up)
                        .all()
                    )
                    existing_runs.sort(key=lambda r: (r.run_date, r.id))
                    db_count = len(existing_runs)

                if db_count >= round_num:
                    target = existing_runs[round_num - 1]
                    changes.append(
                        RowChange(
                            round_key=round_key,
                            zip_code=zip_val,
                            action="update",
                            new_values=new_values,
                            old_values={f: getattr(target, f) for f in METRIC_FIELDS},
                            campaign_run_id=target.id,
                        )
                    )
                elif follow_up_count(zc, zip_val) == round_num - 1:
                    changes.append(
                        RowChange(round_key=round_key, zip_code=zip_val, action="create", new_values=new_values, zip_is_new=zip_is_new)
                    )
                    planned_follow_up_count[zip_val] = round_num
                else:
                    changes.append(
                        RowChange(
                            round_key=round_key,
                            zip_code=zip_val,
                            action="skip",
                            new_values=new_values,
                            warning=(
                                f"Zip {zip_val}: has {planned_follow_up_count[zip_val]} follow-up round(s) recorded "
                                f"(including this upload), so \"{ROUND_LABELS[round_key]}\" can't be added yet - "
                                f"add \"Follow up {planned_follow_up_count[zip_val] + 1}\" first, then re-upload."
                            ),
                        )
                    )

    return changes


def apply_plan(db: Session, changes: List[RowChange]) -> None:
    """Actually writes the changes computed by build_plan(). Caller commits."""
    zip_cache: Dict[str, ZipCode] = {}

    def get_or_create_zip(zip_code: str) -> ZipCode:
        if zip_code in zip_cache:
            return zip_cache[zip_code]
        zc = db.query(ZipCode).filter(ZipCode.zip_code == zip_code).first()
        if not zc:
            zc = ZipCode(zip_code=zip_code)
            db.add(zc)
            db.flush()
        zip_cache[zip_code] = zc
        return zc

    for change in changes:
        if change.action == "skip" or not change.zip_code:
            continue

        zc = get_or_create_zip(change.zip_code)

        if change.action == "update":
            run = db.get(CampaignRun, change.campaign_run_id)
            if not run:
                continue
            for f in METRIC_FIELDS:
                setattr(run, f, change.new_values[f])
        else:  # create
            run_type = RunType.initial if change.round_key == "initial" else RunType.follow_up
            run = CampaignRun(
                zip_code_id=zc.id,
                run_date=date.today(),
                run_type=run_type,
                sms_sent=change.new_values["sms_sent"],
                replies=change.new_values["replies"],
                leads=change.new_values["leads"],
                warm=change.new_values["warm"],
                drip=change.new_values["drip"],
            )
            db.add(run)
            if run_type == RunType.initial:
                if zc.status == ZipStatus.not_tried:
                    zc.status = ZipStatus.active
            else:
                if zc.follow_up_status == ZipStatus.not_tried:
                    zc.follow_up_status = ZipStatus.active

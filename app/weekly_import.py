"""
Weekly VA Excel upload: a 4-tab workbook (First Text, Follow up 1, Follow up
2, Follow up 3), each with Zip Code / SMS Sent / Replies / Hot Leads / Warm
Leads / Drips. Each week's numbers are treated as the current total-to-date
for that zip's round (confirmed with the user), so this UPSERTS - it
overwrites the matching CampaignRun's metrics rather than adding a new row
every week, and creates the round if it doesn't exist yet.

Each tab may also have a "Month Finished" column: a month (date cell or text
like "March 2026"; with several months, the latest counts) becomes the run's
date, and "ONGOING" marks the run as still sending - its zip goes on
Watchlist until every campaign has a finished month, then back to Used
(Tal: "ongoing" and Watchlist mean the same thing). The file wins over
months typed by hand; the preview shows every change. "Started month" is
ignored on purpose.

A zip's follow-up "round number" isn't a stored column anywhere else in the
app (see app/followups.py) - it's purely "the Nth follow-up CampaignRun for
that zip, in (run_date, id) order". "Follow up 2" in the workbook means "the
2nd such row for that zip", matching that same implicit ordering.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

import pandas as pd
from sqlalchemy.orm import Session

from app.database import UPLOAD_ROOT
from app.models import UNSET_DATE, CampaignRun, RunType, ZipCode, ZipStatus

UPLOAD_DIR = UPLOAD_ROOT

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
    "sms_sent": ["sms", "sms_sent", "sms sent", "total messages", "total sent", "messages sent",
                 "total delivery", "total delivered", "total deliveries", "delivered", "delivery"],
    "replies": ["replies", "reply", "total response", "response", "total replies"],
    "leads": ["leads", "lead", "hot lead", "hot leads", "hot"],
    "warm": ["warm", "warm lead", "warm leads"],
    "drip": ["drip", "drips"],
    "finished": ["month finished", "finished month", "finished", "month ended", "end month", "finish month"],
}

METRIC_FIELDS = ["sms_sent", "replies", "leads", "warm", "drip"]
METRIC_LABELS = {
    "sms_sent": "SMS Sent",
    "replies": "Replies",
    "leads": "Hot Leads",
    "warm": "Warm Leads",
    "drip": "Drips",
}


MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def parse_finished(value) -> Tuple[Optional[date], bool]:
    """The "Month Finished" cell -> (finished month, ongoing).
    (None, False) = blank or unreadable: leave the run's date alone."""
    text = str(value or "").strip()
    if not text or text.lower() in ("nan", "none", "nat"):
        return None, False
    if "ongoing" in text.lower():
        return None, True
    found = []
    # Date cells arrive as "2026-03-01 00:00:00"; also accept 3/2026 and 2026-03.
    for y, m in re.findall(r"(20\d\d)-(\d{1,2})-\d{1,2}", text):
        found.append(date(int(y), int(m), 1))
    for m, y in re.findall(r"\b(\d{1,2})/(?:\d{1,2}/)?(20\d\d)\b", text):
        if 1 <= int(m) <= 12:
            found.append(date(int(y), int(m), 1))
    # Month names, typos included ("Februrary 2026"): the first three letters decide.
    for word, y in re.findall(r"([A-Za-z]{3,})\.?[\s,\-/']*(20\d\d|\d\d)\b", text):
        month = MONTHS.get(word[:3].lower())
        if month:
            year = int(y) if len(y) == 4 else 2000 + int(y)
            found.append(date(year, month, 1))
    return (max(found), False) if found else (None, False)


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
    # From "Month Finished": a month, or ongoing. Both empty = column blank/missing.
    finished: Optional[date] = None
    ongoing: bool = False
    old_date: Optional[date] = None
    old_ongoing: bool = False

    @property
    def date_given(self) -> bool:
        return self.finished is not None or self.ongoing

    @property
    def date_changes(self) -> bool:
        if not self.date_given:
            return False
        if self.action != "update":
            return True
        if self.ongoing:
            return not self.old_ongoing
        return self.old_ongoing or self.old_date != self.finished

    @property
    def round_label(self) -> str:
        return ROUND_LABELS[self.round_key]

    @property
    def has_changes(self) -> bool:
        if self.action != "update" or self.old_values is None:
            return True
        return self.date_changes or any(self.old_values.get(f) != self.new_values.get(f) for f in METRIC_FIELDS)


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
            finished, ongoing = parse_finished(row.get(col_map["finished"])) if col_map.get("finished") else (None, False)
            dates = {"finished": finished, "ongoing": ongoing}
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
                            old_date=_real(existing.run_date),
                            old_ongoing=existing.ongoing,
                            **dates,
                        )
                    )
                else:
                    changes.append(
                        RowChange(round_key=round_key, zip_code=zip_val, action="create", new_values=new_values, zip_is_new=zip_is_new, **dates)
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
                            old_date=_real(target.run_date),
                            old_ongoing=target.ongoing,
                            **dates,
                        )
                    )
                elif follow_up_count(zc, zip_val) == round_num - 1:
                    changes.append(
                        RowChange(round_key=round_key, zip_code=zip_val, action="create", new_values=new_values, zip_is_new=zip_is_new, **dates)
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
            _apply_dates(run, change)
        else:  # create
            run_type = RunType.initial if change.round_key == "initial" else RunType.follow_up
            run = CampaignRun(
                zip_code_id=zc.id,
                run_date=date.today().replace(day=1),
                run_type=run_type,
                sms_sent=change.new_values["sms_sent"],
                replies=change.new_values["replies"],
                leads=change.new_values["leads"],
                warm=change.new_values["warm"],
                drip=change.new_values["drip"],
            )
            _apply_dates(run, change)
            db.add(run)
            if run_type == RunType.initial:
                if zc.status == ZipStatus.not_tried:
                    zc.status = ZipStatus.active
            else:
                if zc.follow_up_status == ZipStatus.not_tried:
                    zc.follow_up_status = ZipStatus.active

    db.flush()
    for zc in zip_cache.values():
        sync_watchlist(db, zc)


def _real(d: Optional[date]) -> Optional[date]:
    return d if d and d != UNSET_DATE else None


def _apply_dates(run: CampaignRun, change: RowChange) -> None:
    if change.ongoing:
        run.ongoing = True
    elif change.finished:
        run.ongoing = False
        run.run_date = change.finished


def sync_watchlist(db: Session, zc: ZipCode) -> Optional[str]:
    """Watchlist while any campaign of the zip is still sending; back to Used
    once all have finished. Blacklisted zips are left alone. Returns the new
    status if it changed."""
    if zc.status == ZipStatus.blacklist:
        return None
    ongoing = db.query(CampaignRun).filter(CampaignRun.zip_code_id == zc.id, CampaignRun.ongoing.is_(True)).count() > 0
    if ongoing and zc.status != ZipStatus.watchlist:
        zc.status = ZipStatus.watchlist
        return zc.status
    if not ongoing and zc.status == ZipStatus.watchlist:
        zc.status = ZipStatus.active
        return zc.status
    return None


def planned_status_changes(db: Session, changes: List[RowChange]) -> List[Tuple[str, str, str]]:
    """For the preview: (zip, current status, status after this upload) for zips whose
    Watchlist status would change. Simulates the ongoing flags the upload would set."""
    ongoing_after: Dict[str, Dict[object, bool]] = {}
    for c in changes:
        if c.action == "skip" or not c.zip_code:
            continue
        runs = ongoing_after.setdefault(c.zip_code, {})
        key = c.campaign_run_id or f"new-{len(runs)}"
        if c.ongoing:
            runs[key] = True
        elif c.finished:
            runs[key] = False
        elif c.campaign_run_id is None:
            runs[key] = False
    result = []
    for zip_code, planned in ongoing_after.items():
        zc = db.query(ZipCode).filter(ZipCode.zip_code == zip_code).first()
        current = zc.status if zc else ZipStatus.not_tried
        if current == ZipStatus.blacklist:
            continue
        existing = {r.id: r.ongoing for r in (zc.runs if zc else [])}
        existing.update(planned)
        any_ongoing = any(existing.values())
        if current == ZipStatus.not_tried and not any_ongoing:
            continue  # creating a run makes it Used - not a Watchlist change
        after = ZipStatus.watchlist if any_ongoing else (ZipStatus.active if current == ZipStatus.watchlist else current)
        if after != current:
            result.append((zip_code, str(getattr(current, "value", current)), str(getattr(after, "value", after))))
    return sorted(result)

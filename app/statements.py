"""
Credit-card statement import (Israeli bank Excel export).

The workbook has one tab per billing currency ("חיובים בשקלים", "חיובים בדולרים",
"חיובים באירו") and, inside a tab, one section per card, each with the columns
תאריך חיוב / תאריך העסקה / בית העסק / סכום העסקה / סכום החיוב.

There's no currency column, so on the shekel tab the two amounts tell us what
each row is:
  - charged ≈ 2-5x the transaction amount -> a dollar purchase; the
    transaction amount is the exact dollar figure
  - charged < transaction amount -> installments (תשלומים) in shekels
  - equal -> a shekel purchase
Shekel amounts are converted with one rate per statement, defaulting to the
rate the bank itself used on that statement's dollar rows.

Everything on a statement goes into the statement's month (Tal's choice).
"""

import hashlib
import re
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import openpyxl
from sqlalchemy.orm import Session

from app.finance import CHANNELS, get_or_create_vendor
from app.models import FinanceExpense, FinanceMonth, MatchRule, StatementCharge, StatementImport, Vendor

DEFAULT_ILS_PER_USD = 3.65
USD_RATIO_RANGE = (2.0, 5.0)

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}
HEBREW_MONTHS = {m: i for i, m in enumerate(
    ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט",
     "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"], start=1)}

KIND_LABELS = {
    "usd": "Dollar charge",
    "ils": "Shekel charge",
    "ils_installment": "Shekel installment",
    "usd_billed": "Billed in dollars",
    "other": "Other currency",
}


def _num(value) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").replace("₪", "").replace("$", "").strip())
    except ValueError:
        return None


def _date(value) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d.%m.%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                pass
    return None


def guess_statement_month(filename: str, billing_dates: List[date]) -> date:
    name = filename.lower()
    year_match = re.search(r"(20\d\d)", name)
    for names in (MONTHS, HEBREW_MONTHS):
        for word, num in names.items():
            if word in name:
                year = int(year_match.group(1)) if year_match else (billing_dates[0].year if billing_dates else date.today().year)
                return date(year, num, 1)
    if billing_dates:
        # Bills arrive at the start of the following month.
        b = Counter(billing_dates).most_common(1)[0][0]
        return date(b.year - (b.month == 1), (b.month - 2) % 12 + 1, 1)
    today = date.today()
    return date(today.year, today.month, 1)


def parse_workbook(path: str) -> List[dict]:
    """Returns one dict per charge row, in file order."""
    wb = openpyxl.load_workbook(path, data_only=True)
    rows = []
    for ws in wb.worksheets:
        title = ws.title
        sheet_kind = "usd_billed" if "דולר" in title else "other" if "אירו" in title else "ils"
        cols = None
        card = None
        for r in range(1, ws.max_row + 1):
            values = [ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
            texts = [str(v).strip() if v is not None else "" for v in values]
            joined = " ".join(texts)
            if "ספרות אחרונות" in joined or "חשבון כרטיס" in joined:
                m = re.search(r"ספרות אחרונות\s*(\d{4})", joined)
                card = m.group(1) if m else None
                cols = None
                continue
            if "בית העסק" in texts:
                cols = {name: texts.index(name) for name in texts if name}
                continue
            if not cols or not texts[cols.get("בית העסק", 0)]:
                continue
            merchant = texts[cols["בית העסק"]]
            txn = _num(values[cols["סכום העסקה"]]) if "סכום העסקה" in cols else None
            charged = _num(values[cols["סכום החיוב"]]) if "סכום החיוב" in cols else None
            if txn is None and charged is None:
                continue
            rows.append({
                "card": card,
                "bill_date": _date(values[cols["תאריך חיוב"]]) if "תאריך חיוב" in cols else None,
                "txn_date": _date(values[cols["תאריך העסקה"]]) if "תאריך העסקה" in cols else None,
                "merchant": merchant,
                "txn_amount": txn,
                "charged_amount": charged,
                "sheet_kind": sheet_kind,
            })
    return rows


def classify(row: dict) -> str:
    if row["sheet_kind"] != "ils":
        return row["sheet_kind"]
    txn, charged = row["txn_amount"], row["charged_amount"]
    if txn and charged:
        ratio = charged / txn
        if USD_RATIO_RANGE[0] <= ratio <= USD_RATIO_RANGE[1]:
            return "usd"
        if 0 < ratio < 0.99:
            return "ils_installment"
    return "ils"


def implied_rate(rows: List[dict]) -> Optional[float]:
    ratios = [r["charged_amount"] / r["txn_amount"] for r in rows if r["kind"] == "usd" and r["txn_amount"]]
    return round(statistics.median(ratios), 4) if ratios else None


def usd_amount(kind: str, txn: Optional[float], charged: Optional[float], rate: float) -> Optional[float]:
    if kind == "usd":
        return round(txn, 2)
    if kind == "usd_billed":
        return round(charged if charged is not None else txn, 2)
    if kind in ("ils", "ils_installment") and charged is not None:
        return round(charged / rate, 2)
    return None


def fingerprint(row: dict, occurrence: int) -> str:
    key = "|".join(str(x) for x in (row["card"], row["txn_date"], row["merchant"].strip().upper(),
                                    row["txn_amount"], row["charged_amount"], occurrence))
    return hashlib.sha256(key.encode()).hexdigest()


def pattern_matches(pattern: str, merchant: str) -> bool:
    pattern, text = pattern.strip().upper(), merchant.upper()
    if not pattern:
        return False
    if len(pattern) < 5:
        # Short names ("BOX") only match as a whole word, not inside "DROPBOX".
        return re.search(r"(?<!\w)" + re.escape(pattern) + r"(?!\w)", text) is not None
    return pattern in text


def match_rule(merchant: str, rules: List[MatchRule]) -> Optional[MatchRule]:
    hits = [r for r in rules if pattern_matches(r.pattern or "", merchant)]
    return max(hits, key=lambda r: len(r.pattern)) if hits else None


def suggest_pattern(merchant: str) -> str:
    """A stable part of the merchant name to remember: drops trailing tokens
    that carry order/ad numbers, e.g. 'FACEBK *KGK933E464' -> 'FACEBK'."""
    tokens = merchant.upper().split()
    while len(tokens) > 1 and re.search(r"\d", tokens[-1]):
        tokens.pop()
    return " ".join(tokens)[:120]


def suggest_vendor_name(merchant: str) -> str:
    """A readable vendor name for a new merchant: 'PAYPAL *ACME 123' -> 'Paypal *acme' -> 'Acme'."""
    name = suggest_pattern(merchant)
    name = re.sub(r"^(PAYPAL|SQ|TST|SP|PP)\s*\*\s*", "", name)
    return name.strip(" *").title()[:120] or merchant[:120]


def create_import(db: Session, filename: str, stored_name: str, path: str) -> StatementImport:
    rows = parse_workbook(path)
    for row in rows:
        row["kind"] = classify(row)
    last = db.query(StatementImport).order_by(StatementImport.id.desc()).first()
    rate = implied_rate(rows) or (float(last.ils_per_usd) if last else DEFAULT_ILS_PER_USD)
    imp = StatementImport(
        filename=filename,
        stored_name=stored_name,
        statement_month=guess_statement_month(filename, [r["bill_date"] for r in rows if r["bill_date"]]),
        ils_per_usd=rate,
    )
    db.add(imp)
    db.flush()

    rules = db.query(MatchRule).all()
    seen = Counter()
    for i, row in enumerate(rows):
        key = (row["card"], row["txn_date"], row["merchant"].strip().upper(), row["txn_amount"], row["charged_amount"])
        seen[key] += 1
        fp = fingerprint(row, seen[key])
        earlier = (
            db.query(StatementCharge)
            .join(StatementImport)
            .filter(StatementCharge.fingerprint == fp, StatementImport.committed_at.isnot(None))
            .first()
        )
        charge = StatementCharge(
            row_index=i,
            card=row["card"],
            txn_date=row["txn_date"],
            merchant=row["merchant"],
            txn_amount=row["txn_amount"],
            charged_amount=row["charged_amount"],
            kind=row["kind"],
            usd_amount=usd_amount(row["kind"], row["txn_amount"], row["charged_amount"], rate),
            fingerprint=fp,
        )
        if earlier:
            charge.decision = "skip"
            charge.duplicate_of_import_id = earlier.statement_import_id
            charge.note = "Already imported from an earlier statement"
        else:
            rule = match_rule(row["merchant"], rules)
            if rule and rule.action == "skip":
                charge.decision, charge.matched_rule_id, charge.note = "skip", rule.id, rule.label
            elif rule and rule.vendor:
                charge.decision, charge.matched_rule_id = "vendor", rule.id
                charge.vendor_name = rule.vendor.name
                charge.channel = rule.channel or rule.vendor.default_channel
        imp.charges.append(charge)
    db.flush()
    return imp


def recompute_amounts(imp: StatementImport, rate: float) -> None:
    """When the rate changes, shekel rows follow it (unless edited by hand -
    that's handled by the form sending the edited amount explicitly)."""
    imp.ils_per_usd = rate
    for c in imp.charges:
        c.usd_amount = usd_amount(c.kind, _num(c.txn_amount), _num(c.charged_amount), rate)


def add_months(d: date, n: int) -> date:
    total = d.year * 12 + d.month - 1 + n
    return date(total // 12, total % 12 + 1, 1)


def _month_row(db: Session, month: date) -> FinanceMonth:
    """Creates the month without copying recurring vendors - the statement
    brings the real amounts."""
    fm = db.query(FinanceMonth).filter(FinanceMonth.month == month).first()
    if not fm:
        fm = FinanceMonth(month=month)
        db.add(fm)
        db.flush()
    return fm


def _remember(db: Session, charge: StatementCharge, pattern: str, vendor: Optional[Vendor]) -> None:
    pattern = (pattern or "").strip().upper()
    if len(pattern) < 3:
        return
    rule = db.query(MatchRule).filter(MatchRule.pattern == pattern).first() or MatchRule(pattern=pattern)
    if charge.decision == "skip":
        rule.action, rule.vendor_id, rule.channel, rule.label = "skip", None, None, charge.note or "Not business"
    else:
        rule.action, rule.vendor_id, rule.channel, rule.label = "vendor", vendor.id, charge.channel, None
    db.add(rule)
    db.flush()  # so a second row from the same merchant updates this rule instead of adding another


def commit_import(db: Session, imp: StatementImport, remember: Dict[int, str]) -> dict:
    """Adds the reviewed charges to Finance. `remember` maps charge id -> the
    merchant text to recognize next time."""
    month = imp.statement_month
    grouped: Dict[Tuple[int, str], List[StatementCharge]] = defaultdict(list)
    added_lines, added_total, skipped = 0, 0.0, 0

    for c in imp.charges:
        vendor = None
        if c.decision == "vendor":
            vendor = get_or_create_vendor(db, c.vendor_name, c.channel)
        if c.id in remember:
            _remember(db, c, remember[c.id], vendor)
        if c.decision != "vendor":
            skipped += 1
            continue
        amount = float(c.usd_amount or 0)
        if c.split_months and c.split_months > 1:
            n = c.split_months
            for i in range(n):
                fm = _month_row(db, add_months(month, i))
                db.add(FinanceExpense(
                    finance_month_id=fm.id, vendor_id=vendor.id, channel=c.channel,
                    amount=round(amount / n, 2), source="statement", statement_import_id=imp.id,
                    description=f"Prepaid ${amount:,.2f} on {c.txn_date:%b %d, %Y} for {n} months - already paid ({i + 1}/{n})"
                    + (f". {c.note}" if c.note else ""),
                ))
                added_lines += 1
            added_total += amount
        else:
            grouped[(vendor.id, c.channel)].append(c)

    fm = _month_row(db, month)
    replaced_vendor_ids = set()
    for (vendor_id, channel), charges in grouped.items():
        if vendor_id not in replaced_vendor_ids:
            db.query(FinanceExpense).filter(
                FinanceExpense.finance_month_id == fm.id,
                FinanceExpense.vendor_id == vendor_id,
                FinanceExpense.source == "recurring_copy",
            ).delete()
            replaced_vendor_ids.add(vendor_id)
        total = round(sum(float(c.usd_amount or 0) for c in charges), 2)
        notes = sorted({c.note for c in charges if c.note})
        description = "Card statement" + (f": {len(charges)} charges" if len(charges) > 1 else "")
        if notes:
            description += " - " + "; ".join(notes)
        db.add(FinanceExpense(
            finance_month_id=fm.id, vendor_id=vendor_id, channel=channel, amount=total,
            source="statement", statement_import_id=imp.id, description=description,
        ))
        added_lines += 1
        added_total += total

    imp.committed_at = datetime.utcnow()
    return {"lines": added_lines, "total": round(added_total, 2), "skipped": skipped}


def valid_channel(value: str) -> Optional[str]:
    return value if value in CHANNELS else None

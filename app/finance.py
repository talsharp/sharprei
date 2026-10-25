"""
Wholesale finance: monthly expenses per vendor, leads per channel, and deals.

Revenue is never entered by hand - it's the profit of closed deals, counted in
the month they closed. Deals kept for the portfolio count with their estimated
wholesale profit (Tal's choice), shown as "est." so they stay distinguishable.
Expenses on the "general" channel (hosting, trips, etc.) are shown but are not
wholesale expenses, so they don't reduce wholesale profit either.
"""

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session, selectinload

from app.models import FinanceExpense, FinanceMonth, Vendor, WholesaleDeal

CHANNELS = OrderedDict(
    [
        ("sms", "SMS"),
        ("cold_call", "Cold Call"),
        ("facebook", "Facebook"),
        ("shared", "Wholesale - Shared"),
        ("general", "General"),
    ]
)
LEAD_CHANNELS = ["sms", "cold_call", "facebook"]
WHOLESALE_CHANNELS = [c for c in CHANNELS if c != "general"]

# Which SMS round a deal's lead came from (Finance › Deals "From").
SMS_SOURCES = OrderedDict(
    [
        ("first_text", "First text"),
        ("follow_up_1", "Follow-up 1"),
        ("follow_up_2", "Follow-up 2"),
        ("follow_up_3", "Follow-up 3"),
    ]
)

DEAL_STATUSES = OrderedDict(
    [
        ("under_contract", "Under contract"),
        ("closed", "Closed"),
        ("kept", "Closed - kept for portfolio"),
        ("cancelled", "Dead"),
    ]
)


BUYER_STATUSES = OrderedDict(
    [("waiting", "Waiting"), ("confirmed", "Confirmed"), ("visited", "Visited"), ("cant", "Can't make it")]
)


def add_business_days(start: date, days: int) -> date:
    """The date `days` business days after `start` (weekends skipped, the start day not counted)."""
    d, counted = start, 0
    while counted < days:
        d += timedelta(days=1)
        if d.weekday() < 5:
            counted += 1
    return d


def business_days_until(start: date, end: date) -> int:
    """Business days after `start` up to and including `end` (0 if end isn't later)."""
    count, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return count


@dataclass
class ContractClock:
    days_since_signed: Optional[int] = None
    inspection_end: Optional[date] = None
    business_days_left: Optional[int] = None
    calendar_days_left: Optional[int] = None
    days_to_closing: Optional[int] = None


def contract_clock(deal: WholesaleDeal, today: Optional[date] = None) -> ContractClock:
    today = today or date.today()
    clock = ContractClock()
    if deal.agreement_date:
        clock.days_since_signed = (today - deal.agreement_date).days
        if deal.inspection_days:
            end = add_business_days(deal.agreement_date, deal.inspection_days)
            clock.inspection_end = end
            clock.calendar_days_left = (end - today).days
            clock.business_days_left = business_days_until(today, end) if end >= today else 0
    if deal.closed_date:
        clock.days_to_closing = (deal.closed_date - today).days
    return clock


def month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def next_month(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def parse_month_key(value: str) -> Optional[date]:
    try:
        year, month = value.split("-")[:2]
        return date(int(year), int(month), 1)
    except (ValueError, AttributeError):
        return None


@dataclass
class ChannelStats:
    expenses: float = 0.0
    leads: int = 0
    agreements: int = 0      # deals signed (by agreement date), any status
    closed_deals: int = 0    # closed or kept, by close date
    under_contract: int = 0
    dead: int = 0
    profit: float = 0.0      # profit of this channel's closed deals (its revenue)

    @property
    def cpl(self) -> Optional[float]:
        return self.expenses / self.leads if self.leads else None

    @property
    def net(self) -> float:
        return self.profit - self.expenses

    @property
    def roi(self) -> Optional[float]:
        # Same definition as the overall ROI: (deal profit - spend) / spend.
        return self.net / self.expenses if self.expenses else None

    @property
    def lead_to_agreement(self) -> Optional[float]:
        return self.agreements / self.leads if self.leads else None

    @property
    def agreement_to_deal(self) -> Optional[float]:
        return self.closed_deals / self.agreements if self.agreements else None

    @property
    def lead_to_deal(self) -> Optional[float]:
        return self.closed_deals / self.leads if self.leads else None


@dataclass
class MonthSummary:
    month: date
    finance_month: Optional[FinanceMonth] = None
    wholesale_expenses: float = 0.0
    general_expenses: float = 0.0
    revenue: float = 0.0
    kept_value: float = 0.0
    leads: int = 0
    channels: Dict[str, ChannelStats] = field(default_factory=lambda: {c: ChannelStats() for c in CHANNELS})
    closed_deals: List[WholesaleDeal] = field(default_factory=list)
    kept_deals: List[WholesaleDeal] = field(default_factory=list)
    signed_deals: List[WholesaleDeal] = field(default_factory=list)  # agreement signed this month

    @property
    def profit(self) -> float:
        return self.revenue - self.wholesale_expenses

    @property
    def cpl(self) -> Optional[float]:
        return self.wholesale_expenses / self.leads if self.leads else None


def _channel_leads(fm: FinanceMonth) -> Dict[str, int]:
    return {
        "sms": fm.sms_leads + fm.sms_follow_up_leads,
        "cold_call": fm.cold_call_leads,
        "facebook": fm.facebook_leads,
    }


def build_month_summaries(db: Session) -> "OrderedDict[date, MonthSummary]":
    """Every month that has a finance row or a deal closing in it, newest first."""
    months = db.query(FinanceMonth).options(selectinload(FinanceMonth.expenses)).all()
    deals = db.query(WholesaleDeal).all()

    summaries: Dict[date, MonthSummary] = {}

    def get(m: date) -> MonthSummary:
        if m not in summaries:
            summaries[m] = MonthSummary(month=m)
        return summaries[m]

    for fm in months:
        s = get(fm.month)
        s.finance_month = fm
        for e in fm.expenses:
            amount = float(e.amount or 0)
            s.channels[e.channel].expenses += amount
            if e.channel == "general":
                s.general_expenses += amount
            else:
                s.wholesale_expenses += amount
        for ch, n in _channel_leads(fm).items():
            s.channels[ch].leads = n
        s.leads = fm.total_leads

    for d in deals:
        if d.agreement_date:
            s = get(month_start(d.agreement_date))
            s.signed_deals.append(d)
            if d.channel in s.channels:
                s.channels[d.channel].agreements += 1
        if not d.closed_date or d.status not in ("closed", "kept"):
            continue
        s = get(month_start(d.closed_date))
        s.revenue += d.profit or 0
        if d.channel in s.channels:
            s.channels[d.channel].closed_deals += 1
            s.channels[d.channel].profit += d.profit or 0
        if d.status == "closed":
            s.closed_deals.append(d)
        else:
            s.kept_value += d.profit or 0
            s.kept_deals.append(d)

    return OrderedDict(sorted(summaries.items(), key=lambda kv: kv[0], reverse=True))


@dataclass
class FinanceTotals:
    wholesale_expenses: float = 0.0
    general_expenses: float = 0.0
    revenue: float = 0.0
    kept_value: float = 0.0
    leads: int = 0
    channels: Dict[str, ChannelStats] = field(default_factory=lambda: {c: ChannelStats() for c in CHANNELS})
    signed_agreements: int = 0
    closed_deals: int = 0
    under_contract: int = 0
    closed_without_channel: int = 0
    deals_without_channel: int = 0

    @property
    def profit(self) -> float:
        return self.revenue - self.wholesale_expenses

    @property
    def cpl(self) -> Optional[float]:
        return self.wholesale_expenses / self.leads if self.leads else None

    @property
    def roi(self) -> Optional[float]:
        return self.profit / self.wholesale_expenses if self.wholesale_expenses else None

    @property
    def lead_to_agreement(self) -> Optional[float]:
        return self.signed_agreements / self.leads if self.leads else None

    @property
    def agreement_to_deal(self) -> Optional[float]:
        return self.closed_deals / self.signed_agreements if self.signed_agreements else None

    @property
    def lead_to_deal(self) -> Optional[float]:
        return self.closed_deals / self.leads if self.leads else None


def build_totals(db: Session, summaries: "OrderedDict[date, MonthSummary]") -> FinanceTotals:
    t = FinanceTotals()
    for s in summaries.values():
        t.wholesale_expenses += s.wholesale_expenses
        t.general_expenses += s.general_expenses
        t.revenue += s.revenue
        t.kept_value += s.kept_value
        t.leads += s.leads
        for ch, cs in s.channels.items():
            t.channels[ch].expenses += cs.expenses
            t.channels[ch].leads += cs.leads
    deals = db.query(WholesaleDeal).all()
    t.signed_agreements = len(deals)
    t.closed_deals = sum(1 for d in deals if d.status in ("closed", "kept"))
    t.under_contract = sum(1 for d in deals if d.status == "under_contract")
    for d in deals:
        cs = t.channels.get(d.channel) if d.channel in LEAD_CHANNELS else None
        if cs is None:
            t.deals_without_channel += 1
            if d.status in ("closed", "kept"):
                t.closed_without_channel += 1
            continue
        cs.agreements += 1
        if d.status in ("closed", "kept"):
            cs.closed_deals += 1
            cs.profit += d.profit or 0
        elif d.status == "under_contract":
            cs.under_contract += 1
        elif d.status == "cancelled":
            cs.dead += 1
    return t


def channel_vendors(db: Session, channel: str) -> List[dict]:
    """All-time spend per vendor on one channel, biggest first."""
    rows: Dict[int, dict] = {}
    expenses = (
        db.query(FinanceExpense)
        .options(selectinload(FinanceExpense.vendor), selectinload(FinanceExpense.finance_month))
        .filter(FinanceExpense.channel == channel)
        .all()
    )
    for e in expenses:
        r = rows.setdefault(e.vendor_id, {"vendor": e.vendor, "total": 0.0, "months": set()})
        r["total"] += float(e.amount or 0)
        r["months"].add(e.finance_month.month)
    result = []
    for r in rows.values():
        months = sorted(r["months"])
        result.append({"vendor": r["vendor"], "total": r["total"], "months": len(months),
                       "first": months[0], "last": months[-1], "per_month": r["total"] / len(months)})
    return sorted(result, key=lambda r: -r["total"])


def get_or_create_vendor(db: Session, name: str, channel: Optional[str] = None) -> Vendor:
    name = name.strip()
    vendor = db.query(Vendor).filter(Vendor.name == name).first()
    if not vendor:
        vendor = Vendor(name=name, default_channel=channel if channel in CHANNELS else "shared")
        db.add(vendor)
        db.flush()
    return vendor


def copy_recurring(db: Session, fm: FinanceMonth) -> Optional[FinanceMonth]:
    """Copies recurring vendors' expenses (with their amounts) from the most
    recent earlier month that has any, skipping vendors already in this month.
    Returns the month copied from, or None."""
    source = (
        db.query(FinanceMonth)
        .join(FinanceExpense)
        .join(Vendor)
        .filter(FinanceMonth.month < fm.month, Vendor.recurring.is_(True))
        .order_by(FinanceMonth.month.desc())
        .first()
    )
    if not source:
        return None
    already = {e.vendor_id for e in fm.expenses}
    for e in source.expenses:
        if e.vendor.recurring and e.vendor_id not in already:
            db.add(
                FinanceExpense(
                    finance_month_id=fm.id,
                    vendor_id=e.vendor_id,
                    amount=e.amount,
                    channel=e.channel,
                    description=e.description,
                    source="recurring_copy",
                )
            )
    return source


def start_month(db: Session, month: date) -> FinanceMonth:
    """Creates the month (if new) pre-filled with the recurring vendors from
    the latest month that has them, so a new month is mostly checking amounts."""
    existing = db.query(FinanceMonth).filter(FinanceMonth.month == month).first()
    if existing:
        return existing
    fm = FinanceMonth(month=month)
    db.add(fm)
    db.flush()
    copy_recurring(db, fm)
    return fm


def renegotiation_report(deals) -> dict:
    """Money made by renegotiating the agreement price down. Counted when the
    deal closes (closed or kept), in its closing quarter; under-contract deals
    are pending, dead ones made nothing."""
    quarters: Dict[tuple, dict] = {}
    years: Dict[int, dict] = {}
    pending = {"count": 0, "saved": 0.0}
    for d in deals:
        saved = d.renegotiation_savings
        if not saved:
            continue
        if d.status in ("closed", "kept") and d.closed_date:
            key = (d.closed_date.year, (d.closed_date.month - 1) // 3 + 1)
            for bucket in (quarters.setdefault(key, {"count": 0, "saved": 0.0, "deals": []}),
                           years.setdefault(key[0], {"count": 0, "saved": 0.0})):
                bucket["count"] += 1
                bucket["saved"] += saved
            quarters[key]["deals"].append(d)
        elif d.status == "under_contract":
            pending["count"] += 1
            pending["saved"] += saved
    return {
        "quarters": sorted(quarters.items(), reverse=True),
        "years": sorted(years.items(), reverse=True),
        "pending": pending,
        "total": sum(y["saved"] for y in years.values()),
    }

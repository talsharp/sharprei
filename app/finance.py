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
from datetime import date
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

DEAL_STATUSES = OrderedDict(
    [
        ("under_contract", "Under contract"),
        ("closed", "Closed"),
        ("kept", "Closed - kept for portfolio"),
        ("cancelled", "Didn't close"),
    ]
)


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
    closed_deals: int = 0
    profit: float = 0.0

    @property
    def cpl(self) -> Optional[float]:
        return self.expenses / self.leads if self.leads else None

    @property
    def roi(self) -> Optional[float]:
        return self.profit / self.expenses if self.expenses else None


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
        if not d.closed_date or d.status not in ("closed", "kept"):
            continue
        s = get(month_start(d.closed_date))
        s.revenue += d.profit or 0
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
        if d.status in ("closed", "kept"):
            if d.channel in LEAD_CHANNELS:
                t.channels[d.channel].closed_deals += 1
                t.channels[d.channel].profit += d.profit or 0
            else:
                t.closed_without_channel += 1
    return t


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

"""
Rentals › Projects: deals outside the portfolio, tracked as a money ledger.

Warner Robins agreement (signed 31/12/2025, Tal + Gal & Ran, 50/50 holdings):
- Tal funds everything; 50% of the capital he puts in is a loan to Gal & Ran
  (clause 21). Tal's rule: half of whatever he has in at any moment is the
  loan, so money back to him (e.g. the July 2026 refinance) lowers it by half.
- Annual payment = 4.5% of the loan (clause 22B), paid once at the end of each
  year of activity (22D). When the loan changes mid-year, the payment changes
  with it from that date (22E) - so each year is time-weighted by day.
- No interest on interest, no indexation (22H).
- A loan balance left when the partnership ends is paid back as principal only
  (clause 31).
"""

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Optional

# What each kind of payment means. "loan" = how it moves the loan to Gal & Ran:
# half = half the amount (Tal's money in/out), full = the whole amount.
PAYMENT_KINDS = OrderedDict([
    ("tal_in", {"label": "Tal put money in", "loan": +0.5}),
    ("tal_back", {"label": "Money back to Tal", "loan": -0.5}),
    ("annual_payment", {"label": "Annual payment to Tal (4.5%)", "loan": 0}),
    ("loan_repay", {"label": "Gal & Ran repaid loan", "loan": -1}),
    ("partner_in", {"label": "Gal & Ran put money in", "loan": 0}),
    ("partner_out", {"label": "Gal & Ran took money out", "loan": 0}),
    ("other", {"label": "Other", "loan": 0}),
])

DEFAULT_ACCOUNTS = ["Private account", "Sharphead LTD"]
DEFAULT_PARTIES = ["Tal", "Gal & Ran", "Project account", "Title company"]


def _amount(p) -> float:
    return float(p.amount or 0)


def loan_change(p) -> float:
    """How much this payment moves the loan. A netted annual payment (taken off
    money Tal paid them, so nothing moved) counts as more money Tal put in."""
    if p.kind == "annual_payment" and p.netted:
        return _amount(p) * 0.5
    return _amount(p) * PAYMENT_KINDS.get(p.kind, PAYMENT_KINDS["other"])["loan"]


def add_year(d: date) -> date:
    try:
        return d.replace(year=d.year + 1)
    except ValueError:  # Feb 29
        return d.replace(year=d.year + 1, day=28)


@dataclass
class Period:
    start: date
    end: date
    loan: float
    days: int
    owed: float


@dataclass
class LoanYear:
    number: int
    start: date
    end: date
    finished: bool
    periods: List[Period] = field(default_factory=list)
    owed: float = 0.0
    full_year: float = 0.0  # what this year comes to if the loan stays as it is today
    paid: float = 0.0


@dataclass
class ProjectSummary:
    tal_in: float = 0.0
    tal_back: float = 0.0
    tal_net: float = 0.0
    tal_share: float = 0.0
    loan: float = 0.0
    loan_repaid: float = 0.0
    annual_paid: float = 0.0
    owed_finished: float = 0.0
    owed_this_year: float = 0.0
    still_owed: float = 0.0
    partner_in: float = 0.0
    partner_out: float = 0.0
    rate: float = 0.0
    start: Optional[date] = None
    years: List[LoanYear] = field(default_factory=list)
    by_year: "OrderedDict[int, dict]" = field(default_factory=OrderedDict)
    unrecognised: int = 0


def summarize(project, today: date) -> ProjectSummary:
    s = ProjectSummary(rate=float(project.loan_rate or 0))
    dated = [p for p in project.payments if p.paid_on]
    for p in project.payments:
        a = _amount(p)
        if p.kind == "tal_in" or (p.kind == "annual_payment" and p.netted):
            s.tal_in += a
        elif p.kind == "tal_back":
            s.tal_back += a
        elif p.kind == "loan_repay":
            s.loan_repaid += a
        elif p.kind == "partner_in":
            s.partner_in += a
        elif p.kind == "partner_out":
            s.partner_out += a
        if p.kind == "annual_payment":
            s.annual_paid += a
        if p.paid_on:
            row = s.by_year.setdefault(p.paid_on.year, {k: 0.0 for k in PAYMENT_KINDS})
            row[p.kind if p.kind in PAYMENT_KINDS else "other"] += a
        if not p.paid_on and loan_change(p):
            s.unrecognised += 1  # no date = can't place it on the loan timeline
    s.by_year = OrderedDict(sorted(s.by_year.items()))
    s.tal_net = s.tal_in - s.tal_back - s.loan_repaid
    s.tal_share = (s.tal_in - s.tal_back) / 2
    s.loan = sum(loan_change(p) for p in project.payments)

    starts = [p.paid_on for p in dated if loan_change(p)]
    s.start = project.loan_start or (min(starts) if starts else None)
    if not s.start or s.start > today:
        s.still_owed = -s.annual_paid
        return s

    changes = sorted((p.paid_on, loan_change(p)) for p in dated if loan_change(p))
    rate = s.rate / 100

    def loan_on(day: date) -> float:
        return sum(c for d, c in changes if d <= day)

    year_start, n = s.start, 1
    while year_start <= today:
        year_end = add_year(year_start)
        y = LoanYear(number=n, start=year_start, end=year_end, finished=year_end <= today)
        stop = min(year_end, today) if not y.finished else year_end
        # Split the year wherever the loan changed.
        cuts = sorted({year_start, stop} | {d for d, _ in changes if year_start < d < stop})
        for a, b in zip(cuts, cuts[1:]):
            loan = max(0.0, loan_on(a))
            days = (b - a).days
            owed = loan * rate * days / 365
            y.periods.append(Period(a, b, loan, days, owed))
            y.owed += owed
        if not y.finished:
            # Rest of the year at today's loan, to show what it's heading for.
            y.full_year = y.owed + max(0.0, loan_on(today)) * rate * (year_end - stop).days / 365
        y.paid = sum(_amount(p) for p in dated if p.kind == "annual_payment" and year_start <= p.paid_on < year_end)
        s.years.append(y)
        if y.finished:
            s.owed_finished += y.owed
        else:
            s.owed_this_year = y.owed
        year_start, n = year_end, n + 1

    s.still_owed = s.owed_finished - s.annual_paid
    return s

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.metrics import _label_ratio, _ratio_to_average
from app.models import CampaignRun, Deal, RunType


@dataclass
class FollowUpRound:
    round_number: int
    run: CampaignRun
    days_since_initial: Optional[int]
    deal_count: int = 0
    total_profit: float = 0.0
    avg_reply_rate: Optional[float] = None
    avg_lead_rate: Optional[float] = None
    avg_warm_rate: Optional[float] = None
    avg_drip_rate: Optional[float] = None
    reply_vs_avg: str = "n/a"
    lead_vs_avg: str = "n/a"
    warm_vs_avg: str = "n/a"
    drip_vs_avg: str = "n/a"

    @property
    def reply_rate(self) -> float:
        return self.run.replies / self.run.sms_sent if self.run.sms_sent else 0.0

    @property
    def lead_rate(self) -> float:
        return self.run.leads / self.run.sms_sent if self.run.sms_sent else 0.0

    @property
    def warm_rate(self) -> float:
        return self.run.warm / self.run.replies if self.run.replies else 0.0

    @property
    def drip_rate(self) -> float:
        return self.run.drip / self.run.replies if self.run.replies else 0.0


@dataclass
class FollowUpSummary:
    rounds: List[FollowUpRound] = field(default_factory=list)
    total_sms: int = 0
    total_replies: int = 0
    total_leads: int = 0
    total_warm: int = 0
    total_drip: int = 0
    total_signed_agreements: int = 0
    deal_count: int = 0
    total_profit: float = 0.0
    avg_reply_rate: Optional[float] = None
    avg_lead_rate: Optional[float] = None
    avg_warm_rate: Optional[float] = None
    avg_drip_rate: Optional[float] = None
    reply_vs_avg: str = "n/a"
    lead_vs_avg: str = "n/a"
    warm_vs_avg: str = "n/a"
    drip_vs_avg: str = "n/a"

    @property
    def reply_rate(self) -> float:
        return self.total_replies / self.total_sms if self.total_sms else 0.0

    @property
    def lead_rate(self) -> float:
        return self.total_leads / self.total_sms if self.total_sms else 0.0

    @property
    def warm_rate(self) -> float:
        return self.total_warm / self.total_replies if self.total_replies else 0.0

    @property
    def drip_rate(self) -> float:
        return self.total_drip / self.total_replies if self.total_replies else 0.0


def _apply_vs_average(items, rate_props=("reply_rate", "lead_rate", "warm_rate", "drip_rate")) -> None:
    """Set avg_<rate> and <rate>_vs_avg on every item, comparing each item's rate
    to the average of that same rate across all items in the given group."""
    items = list(items)
    if not items:
        return
    for prop in rate_props:
        avg_value = sum(getattr(it, prop) for it in items) / len(items)
        avg_attr = "avg_" + prop
        vs_avg_attr = prop.replace("_rate", "_vs_avg")
        for it in items:
            setattr(it, avg_attr, avg_value)
            setattr(it, vs_avg_attr, _label_ratio(_ratio_to_average(getattr(it, prop), avg_value)))


def get_follow_up_summaries(db: Session, zip_ids: Optional[List[int]] = None) -> Dict[int, FollowUpSummary]:
    """Always computed over ALL zip codes with follow-up data (so averages are
    meaningful), then filtered down to `zip_ids` at the very end if given."""
    followup_runs = db.query(CampaignRun).filter(CampaignRun.run_type == RunType.follow_up).all()
    followup_run_ids = [r.id for r in followup_runs]

    deals_by_run: Dict[int, List[Deal]] = defaultdict(list)
    if followup_run_ids:
        deal_stmt = select(Deal).where(Deal.campaign_run_id.in_(followup_run_ids))
        for deal in db.execute(deal_stmt).scalars().all():
            deals_by_run[deal.campaign_run_id].append(deal)

    # Reference date per zip: the earliest INITIAL run, so "days since initial"
    # is anchored to the initial send even if the run rows arrive out of order.
    initial_dates_by_zip: Dict[int, date] = {}
    for run in db.query(CampaignRun).filter(CampaignRun.run_type == RunType.initial).all():
        current = initial_dates_by_zip.get(run.zip_code_id)
        if current is None or run.run_date < current:
            initial_dates_by_zip[run.zip_code_id] = run.run_date

    runs_by_zip = defaultdict(list)
    for run in followup_runs:
        runs_by_zip[run.zip_code_id].append(run)

    result: Dict[int, FollowUpSummary] = {}
    for zip_id, runs in runs_by_zip.items():
        reference_date = initial_dates_by_zip.get(zip_id) or min(r.run_date for r in runs)
        runs.sort(key=lambda r: r.run_date)

        summary = FollowUpSummary()
        for i, run in enumerate(runs, start=1):
            days_since = (run.run_date - reference_date).days if reference_date else None
            round_deals = deals_by_run.get(run.id, [])
            round_profit = sum(float(d.profit) for d in round_deals)
            summary.rounds.append(
                FollowUpRound(
                    round_number=i,
                    run=run,
                    days_since_initial=days_since,
                    deal_count=len(round_deals),
                    total_profit=round_profit,
                )
            )
            summary.total_sms += run.sms_sent
            summary.total_replies += run.replies
            summary.total_leads += run.leads
            summary.total_warm += run.warm
            summary.total_drip += run.drip
            summary.total_signed_agreements += run.signed_agreements
            summary.deal_count += len(round_deals)
            summary.total_profit += round_profit

        result[zip_id] = summary

    # Compare each round to the average for that SAME round number across zips
    # (round 1 vs round 1, round 2 vs round 2, ...), not against other rounds.
    rounds_by_number: Dict[int, List[FollowUpRound]] = defaultdict(list)
    for summary in result.values():
        for rnd in summary.rounds:
            rounds_by_number[rnd.round_number].append(rnd)
    for rounds in rounds_by_number.values():
        _apply_vs_average(rounds)

    # Compare each zip's all-rounds-combined total to other zips' totals.
    _apply_vs_average(result.values())

    if zip_ids is not None:
        result = {zid: s for zid, s in result.items() if zid in set(zip_ids)}

    return result


def get_rounds_overview(
    db: Session,
) -> Tuple[List[int], Dict[int, List[FollowUpRound]], List[FollowUpSummary]]:
    summaries = get_follow_up_summaries(db)

    rounds_by_number: Dict[int, List[FollowUpRound]] = defaultdict(list)
    for summary in summaries.values():
        for rnd in summary.rounds:
            rounds_by_number[rnd.round_number].append(rnd)
    for rounds in rounds_by_number.values():
        rounds.sort(key=lambda r: r.run.zip_code.zip_code)
    round_numbers = sorted(rounds_by_number.keys())

    summaries_sorted = sorted(
        summaries.values(),
        key=lambda s: s.rounds[0].run.zip_code.zip_code if s.rounds else "",
    )

    return round_numbers, dict(rounds_by_number), summaries_sorted

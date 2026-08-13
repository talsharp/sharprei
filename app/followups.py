from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CampaignRun, Deal, RunType


@dataclass
class FollowUpRound:
    round_number: int
    run: CampaignRun
    days_since_initial: Optional[int]
    deal_count: int = 0
    total_profit: float = 0.0

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


def get_follow_up_summaries(db: Session, zip_ids: Optional[List[int]] = None) -> Dict[int, FollowUpSummary]:
    query = db.query(CampaignRun).filter(CampaignRun.run_type == RunType.follow_up)
    if zip_ids is not None:
        query = query.filter(CampaignRun.zip_code_id.in_(zip_ids))
    followup_runs = query.all()
    followup_run_ids = [r.id for r in followup_runs]

    deals_by_run: Dict[int, List[Deal]] = defaultdict(list)
    if followup_run_ids:
        deal_stmt = select(Deal).where(Deal.campaign_run_id.in_(followup_run_ids))
        for deal in db.execute(deal_stmt).scalars().all():
            deals_by_run[deal.campaign_run_id].append(deal)

    # Reference date per zip: the earliest INITIAL run, so "days since initial"
    # is anchored to the initial send even if the run rows arrive out of order.
    initial_dates_by_zip: Dict[int, date] = {}
    initial_query = db.query(CampaignRun).filter(CampaignRun.run_type == RunType.initial)
    if zip_ids is not None:
        initial_query = initial_query.filter(CampaignRun.zip_code_id.in_(zip_ids))
    for run in initial_query.all():
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

    return result

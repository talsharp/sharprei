from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models import CampaignRun, RunType


@dataclass
class FollowUpRound:
    round_number: int
    run: CampaignRun
    days_since_initial: Optional[int]


@dataclass
class FollowUpSummary:
    rounds: List[FollowUpRound] = field(default_factory=list)
    total_sms: int = 0
    total_replies: int = 0
    total_leads: int = 0
    total_warm: int = 0
    total_drip: int = 0
    total_signed_agreements: int = 0


def get_follow_up_summaries(db: Session, zip_ids: Optional[List[int]] = None) -> Dict[int, FollowUpSummary]:
    query = db.query(CampaignRun)
    if zip_ids is not None:
        query = query.filter(CampaignRun.zip_code_id.in_(zip_ids))

    runs_by_zip = defaultdict(list)
    for run in query.all():
        runs_by_zip[run.zip_code_id].append(run)

    result: Dict[int, FollowUpSummary] = {}
    for zip_id, runs in runs_by_zip.items():
        initial_dates = [r.run_date for r in runs if r.run_type == RunType.initial]
        reference_date = min(initial_dates) if initial_dates else (
            min((r.run_date for r in runs), default=None)
        )

        followups = sorted(
            (r for r in runs if r.run_type == RunType.follow_up), key=lambda r: r.run_date
        )
        if not followups:
            continue

        summary = FollowUpSummary()
        for i, run in enumerate(followups, start=1):
            days_since = (run.run_date - reference_date).days if reference_date else None
            summary.rounds.append(
                FollowUpRound(round_number=i, run=run, days_since_initial=days_since)
            )
            summary.total_sms += run.sms_sent
            summary.total_replies += run.replies
            summary.total_leads += run.leads
            summary.total_warm += run.warm
            summary.total_drip += run.drip
            summary.total_signed_agreements += run.signed_agreements

        result[zip_id] = summary

    return result

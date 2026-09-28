from dataclasses import dataclass
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CampaignRun, Deal, RunType, WholesaleDeal, ZipCode

# Zips with at least this many SMS set the "average zip" that each zip's
# rates are compared against in the hover text on the % columns.
MIN_SMS_FOR_AVERAGE = 500

# Score: how a zip compares to the average zip, 100 = average. Each rate is
# per message sent, weighted by importance (leads matter most, then replies,
# then a little drips). Warm leads are deliberately not part of it.
SCORE_WEIGHTS = {"leads": 0.6, "replies": 0.3, "drip": 0.1}
# Small-sample protection: every zip is scored as if it also had this many
# extra messages performing exactly at average, so a zip with few messages is
# pulled toward 100 until its own volume is big enough to trust.
SCORE_PRIOR_SMS = 500

# How far a metric's ratio-to-average has to be to count as "well above" / "well below"
# average, vs. just "roughly average" - used for the hover text on the % columns.
FAR_ABOVE_AVERAGE = 1.5
ABOVE_AVERAGE = 1.15
BELOW_AVERAGE = 0.85


def score_group(rows: List[Tuple[int, int, int, int]]) -> List[Optional[float]]:
    """rows are (sms, leads, replies, drip) for every zip in one comparison
    group (initial sends, or one follow-up round). Returns each zip's score,
    or None for zips with no messages sent."""
    total_sms = sum(r[0] for r in rows)
    if total_sms <= 0:
        return [None for _ in rows]
    averages = [sum(r[i] for r in rows) / total_sms for i in (1, 2, 3)]
    weights = [SCORE_WEIGHTS["leads"], SCORE_WEIGHTS["replies"], SCORE_WEIGHTS["drip"]]
    scores = []
    for sms, *counts in rows:
        if not sms:
            scores.append(None)
            continue
        total = 0.0
        for count, avg, weight in zip(counts, averages, weights):
            adjusted = (count + SCORE_PRIOR_SMS * avg) / (sms + SCORE_PRIOR_SMS)
            total += weight * (adjusted / avg if avg > 0 else 1.0)
        scores.append(total * 100)
    return scores


@dataclass
class ZipMetrics:
    zip_code: ZipCode
    total_sms: int = 0
    total_replies: int = 0
    total_leads: int = 0
    total_warm: int = 0
    total_drip: int = 0
    total_signed_agreements: int = 0
    total_opt_out: Optional[int] = None
    run_count: int = 0
    deal_count: int = 0
    total_profit: float = 0.0
    score: Optional[float] = None
    avg_lead_rate: Optional[float] = None
    avg_reply_rate: Optional[float] = None
    avg_warm_rate: Optional[float] = None
    avg_drip_rate: Optional[float] = None
    lead_vs_avg: str = "n/a"
    reply_vs_avg: str = "n/a"
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

    @property
    def optout_rate(self) -> Optional[float]:
        if self.total_opt_out is None or not self.total_sms:
            return None
        return self.total_opt_out / self.total_sms


def _ratio_to_average(value: float, average: float) -> float:
    """How many times `value` is over `average`. If the average itself is 0,
    any positive value counts as clearly above average, and 0 counts as average."""
    if average > 0:
        return value / average
    return 1.5 if value > 0 else 1.0


def _label_ratio(ratio: float) -> str:
    if ratio >= FAR_ABOVE_AVERAGE:
        return "well above avg"
    if ratio > ABOVE_AVERAGE:
        return "above avg"
    if ratio >= BELOW_AVERAGE:
        return "avg"
    return "below avg"


def _assign_comparisons(results: List[ZipMetrics]) -> None:
    eligible = [m for m in results if m.total_sms >= MIN_SMS_FOR_AVERAGE]
    if not eligible:
        return

    def avg(prop: str) -> float:
        values = [getattr(m, prop) for m in eligible]
        return sum(values) / len(values)

    avg_lead_rate = avg("lead_rate")
    avg_reply_rate = avg("reply_rate")
    avg_warm_rate = avg("warm_rate")
    avg_drip_rate = avg("drip_rate")

    for m in results:
        m.avg_lead_rate = avg_lead_rate
        m.avg_reply_rate = avg_reply_rate
        m.avg_warm_rate = avg_warm_rate
        m.avg_drip_rate = avg_drip_rate
        m.lead_vs_avg = _label_ratio(_ratio_to_average(m.lead_rate, avg_lead_rate))
        m.reply_vs_avg = _label_ratio(_ratio_to_average(m.reply_rate, avg_reply_rate))
        m.warm_vs_avg = _label_ratio(_ratio_to_average(m.warm_rate, avg_warm_rate))
        m.drip_vs_avg = _label_ratio(_ratio_to_average(m.drip_rate, avg_drip_rate))


def get_zip_metrics(db: Session, zip_code_id: Optional[int] = None) -> List[ZipMetrics]:
    """Metrics here reflect INITIAL sends only - follow-up performance is tracked
    separately (see app.followups) and is never blended into these numbers,
    including the score, so a zip's score always reflects its proven
    initial-send performance regardless of how much follow-up activity it's had."""
    run_agg = (
        select(
            CampaignRun.zip_code_id.label("zip_code_id"),
            func.sum(CampaignRun.sms_sent).label("total_sms"),
            func.sum(CampaignRun.replies).label("total_replies"),
            func.sum(CampaignRun.leads).label("total_leads"),
            func.sum(CampaignRun.warm).label("total_warm"),
            func.sum(CampaignRun.drip).label("total_drip"),
            func.sum(CampaignRun.signed_agreements).label("total_signed_agreements"),
            func.sum(CampaignRun.opt_out).label("total_opt_out"),
            func.count(CampaignRun.id).label("run_count"),
        )
        .where(CampaignRun.run_type == RunType.initial)
        .group_by(CampaignRun.zip_code_id)
        .subquery()
    )

    deal_agg = (
        select(
            CampaignRun.zip_code_id.label("zip_code_id"),
            func.count(Deal.id).label("deal_count"),
            func.sum(Deal.profit).label("total_profit"),
        )
        .join(Deal, Deal.campaign_run_id == CampaignRun.id)
        .where(CampaignRun.run_type == RunType.initial)
        .group_by(CampaignRun.zip_code_id)
        .subquery()
    )

    stmt = (
        select(
            ZipCode,
            run_agg.c.total_sms,
            run_agg.c.total_replies,
            run_agg.c.total_leads,
            run_agg.c.total_warm,
            run_agg.c.total_drip,
            run_agg.c.total_signed_agreements,
            run_agg.c.total_opt_out,
            run_agg.c.run_count,
            deal_agg.c.deal_count,
            deal_agg.c.total_profit,
        )
        .outerjoin(run_agg, run_agg.c.zip_code_id == ZipCode.id)
        .outerjoin(deal_agg, deal_agg.c.zip_code_id == ZipCode.id)
    )

    # Closed deals from Finance > Deals that were tagged with a zip code count
    # toward that zip too (kept-for-portfolio houses count at their est. profit).
    finance_deals = {}
    for d in db.query(WholesaleDeal).filter(WholesaleDeal.status.in_(("closed", "kept")), WholesaleDeal.zip_code_id.isnot(None)):
        count, profit = finance_deals.get(d.zip_code_id, (0, 0.0))
        finance_deals[d.zip_code_id] = (count + 1, profit + (d.profit or 0))

    results = []
    for row in db.execute(stmt).all():
        (
            zc,
            total_sms,
            total_replies,
            total_leads,
            total_warm,
            total_drip,
            total_signed_agreements,
            total_opt_out,
            run_count,
            deal_count,
            total_profit,
        ) = row
        results.append(
            ZipMetrics(
                zip_code=zc,
                total_sms=total_sms or 0,
                total_replies=total_replies or 0,
                total_leads=total_leads or 0,
                total_warm=total_warm or 0,
                total_drip=total_drip or 0,
                total_signed_agreements=total_signed_agreements or 0,
                total_opt_out=total_opt_out,
                run_count=run_count or 0,
                deal_count=(deal_count or 0) + finance_deals.get(zc.id, (0, 0.0))[0],
                total_profit=float(total_profit or 0) + finance_deals.get(zc.id, (0, 0.0))[1],
            )
        )

    # Scores are always computed against the full dataset, so a single zip
    # (e.g. the detail page) still reflects its real standing.
    scores = score_group([(m.total_sms, m.total_leads, m.total_replies, m.total_drip) for m in results])
    for m, score in zip(results, scores):
        m.score = score
    _assign_comparisons(results)

    if zip_code_id is not None:
        results = [m for m in results if m.zip_code.id == zip_code_id]

    return results

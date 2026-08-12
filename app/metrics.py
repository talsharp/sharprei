from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CampaignRun, Deal, ZipCode

# Minimum SMS volume before a zip code is eligible for a tier - below this,
# lead/reply rates are too noisy on a small sample to grade reliably.
MIN_SMS_FOR_TIER = 500

# Weights match the priority order: leads matter most, then replies, then warm, then drip.
SCORE_WEIGHTS = {"lead_rate": 0.4, "reply_rate": 0.3, "warm_rate": 0.2, "drip_rate": 0.1}

# Relative weight of each "secondary" signal (replies > warm > drip), used to blend them
# into one combined ratio against their averages.
SECONDARY_WEIGHTS = {"reply_rate": 0.5, "warm_rate": 0.333, "drip_rate": 0.167}

# How far a metric's ratio-to-average has to be to count as "well above" / "well below"
# average, vs. just "roughly average". These are starting defaults - tune freely.
FAR_ABOVE_AVERAGE = 1.5
ABOVE_AVERAGE = 1.15
BELOW_AVERAGE = 0.85

TIER_ORDER = ["Great", "Good", "OK", "Not Good", "Bad"]
INSUFFICIENT_DATA_TIER = "Not enough data"


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
    score: float = 0.0
    computed_tier: str = INSUFFICIENT_DATA_TIER
    tier: str = INSUFFICIENT_DATA_TIER
    tier_reason: str = "Not enough data yet to compare against the average."
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


def _normalize(values: List[float]) -> List[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


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


def _classify_tier(lead_ratio: float, secondary_ratio: float, reply_ratio: float) -> str:
    if lead_ratio >= FAR_ABOVE_AVERAGE:
        return "Great"
    if lead_ratio > ABOVE_AVERAGE and secondary_ratio >= FAR_ABOVE_AVERAGE - 0.2:
        return "Great"
    if lead_ratio > ABOVE_AVERAGE:
        return "Good"
    if lead_ratio >= BELOW_AVERAGE:
        # Roughly average leads - a clearly stronger secondary signal still bumps it up.
        return "Good" if secondary_ratio > ABOVE_AVERAGE else "OK"
    # Leads are below average. If replies (or the blended secondary signal) are still
    # solid, this could just be luck of the draw so far - worth continuing to test,
    # rather than writing it off as Bad.
    if reply_ratio > ABOVE_AVERAGE or secondary_ratio > 1.0:
        return "Not Good"
    return "Bad"


def _assign_tiers(results: List[ZipMetrics]) -> None:
    eligible = [m for m in results if m.total_sms >= MIN_SMS_FOR_TIER]
    if not eligible:
        return

    def avg(prop: str) -> float:
        values = [getattr(m, prop) for m in eligible]
        return sum(values) / len(values)

    avg_lead_rate = avg("lead_rate")
    avg_reply_rate = avg("reply_rate")
    avg_warm_rate = avg("warm_rate")
    avg_drip_rate = avg("drip_rate")

    # Every zip code gets a vs-average breakdown (useful context even below the
    # tiering threshold) - only eligible ones get an actual tier assigned.
    for m in results:
        m.avg_lead_rate = avg_lead_rate
        m.avg_reply_rate = avg_reply_rate
        m.avg_warm_rate = avg_warm_rate
        m.avg_drip_rate = avg_drip_rate

        lead_ratio = _ratio_to_average(m.lead_rate, avg_lead_rate)
        reply_ratio = _ratio_to_average(m.reply_rate, avg_reply_rate)
        warm_ratio = _ratio_to_average(m.warm_rate, avg_warm_rate)
        drip_ratio = _ratio_to_average(m.drip_rate, avg_drip_rate)

        m.lead_vs_avg = _label_ratio(lead_ratio)
        m.reply_vs_avg = _label_ratio(reply_ratio)
        m.warm_vs_avg = _label_ratio(warm_ratio)
        m.drip_vs_avg = _label_ratio(drip_ratio)

        reason = (
            f"Leads: {m.lead_vs_avg} | Replies: {m.reply_vs_avg} | "
            f"Warm: {m.warm_vs_avg} | Drip: {m.drip_vs_avg}"
        )
        if m.total_sms < MIN_SMS_FOR_TIER:
            reason = f"Only {m.total_sms} SMS sent (min {MIN_SMS_FOR_TIER} for a tier). {reason}"
        m.tier_reason = reason

        if m.total_sms >= MIN_SMS_FOR_TIER:
            secondary_ratio = (
                reply_ratio * SECONDARY_WEIGHTS["reply_rate"]
                + warm_ratio * SECONDARY_WEIGHTS["warm_rate"]
                + drip_ratio * SECONDARY_WEIGHTS["drip_rate"]
            )
            m.computed_tier = _classify_tier(lead_ratio, secondary_ratio, reply_ratio)
            m.tier = m.zip_code.tier_override or m.computed_tier


def get_zip_metrics(db: Session, zip_code_id: Optional[int] = None) -> List[ZipMetrics]:
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
                deal_count=deal_count or 0,
                total_profit=float(total_profit or 0),
            )
        )

    # Score is kept as a secondary continuous reference (used for sorting) - it's
    # always computed against the full dataset, so a single zip code (e.g. the
    # detail page) still reflects its real standing.
    lead_rates = _normalize([m.lead_rate for m in results])
    reply_rates = _normalize([m.reply_rate for m in results])
    warm_rates = _normalize([m.warm_rate for m in results])
    drip_rates = _normalize([m.drip_rate for m in results])
    for m, lr, rr, wr, dr in zip(results, lead_rates, reply_rates, warm_rates, drip_rates):
        m.score = (
            lr * SCORE_WEIGHTS["lead_rate"]
            + rr * SCORE_WEIGHTS["reply_rate"]
            + wr * SCORE_WEIGHTS["warm_rate"]
            + dr * SCORE_WEIGHTS["drip_rate"]
        ) * 100

    _assign_tiers(results)

    if zip_code_id is not None:
        results = [m for m in results if m.zip_code.id == zip_code_id]

    return results

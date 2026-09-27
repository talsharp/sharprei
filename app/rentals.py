import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from sqlalchemy.orm import Session

from app.database import UPLOAD_ROOT
from app.models import MonthlyExpense, Property

INSUFFICIENT_DATA = "Not enough data"

RENOVATION_UPLOAD_DIR = UPLOAD_ROOT / "renovations"
RENOVATION_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def save_renovation_file(filename: str, content: bytes) -> str:
    token = f"{uuid.uuid4().hex}_{filename}"
    (RENOVATION_UPLOAD_DIR / token).write_bytes(content)
    return token


@dataclass
class PropertyYield:
    """Cash-on-cash return: average monthly net cash flow across every
    recorded month, annualized, divided by total invested. Updates as more
    months are added rather than requiring a full year of history."""

    months_recorded: int = 0
    avg_monthly_net_cash_flow: float = 0.0
    annual_net_cash_flow: float = 0.0
    total_invested: float = 0.0
    total_income: float = 0.0
    total_expenses: float = 0.0
    yield_pct: Optional[float] = None
    est_yield_pct: Optional[float] = None

    @property
    def display(self) -> str:
        return f"{self.yield_pct:.2f}%" if self.yield_pct is not None else INSUFFICIENT_DATA

    @property
    def est_yield_display(self) -> str:
        return f"{self.est_yield_pct:.2f}%" if self.est_yield_pct is not None else "Not Occupied"


# Steady-state expense assumption for Est. Yield: the classic real-estate
# "50% rule" tuned to 45% - i.e. 45% of gross rent goes to vacancy, repairs,
# management, taxes, insurance etc. once a property is past its first-year
# move-in costs, leaving 55% as net operating income.
EST_YIELD_EXPENSE_RATIO = 0.45


def _est_yield_pct(prop: Property) -> Optional[float]:
    if not prop.rent_price or float(prop.rent_price) <= 0 or prop.total_invested <= 0:
        return None
    annual_noi = float(prop.rent_price) * 12 * (1 - EST_YIELD_EXPENSE_RATIO)
    return (annual_noi / prop.total_invested) * 100


def get_property_yield(prop: Property) -> PropertyYield:
    months = prop.monthly_expenses
    total_invested = prop.total_invested
    total_income = sum(float(m.income) for m in months)
    total_expenses = sum(m.total_expenses for m in months)
    est_yield_pct = _est_yield_pct(prop)
    if not months or total_invested <= 0:
        return PropertyYield(
            months_recorded=len(months),
            total_invested=total_invested,
            total_income=total_income,
            total_expenses=total_expenses,
            est_yield_pct=est_yield_pct,
        )

    avg_monthly = sum(m.net_cash_flow for m in months) / len(months)
    annual = avg_monthly * 12
    return PropertyYield(
        months_recorded=len(months),
        avg_monthly_net_cash_flow=avg_monthly,
        annual_net_cash_flow=annual,
        total_invested=total_invested,
        total_income=total_income,
        total_expenses=total_expenses,
        yield_pct=(annual / total_invested) * 100,
        est_yield_pct=est_yield_pct,
    )


@dataclass
class PortfolioSummary:
    property_count: int = 0
    occupied_count: int = 0
    total_purchase_price: float = 0.0
    total_renovation_costs: float = 0.0
    total_invested: float = 0.0
    total_max_arv: float = 0.0
    total_estimated_value: float = 0.0
    total_estimated_profit: float = 0.0
    # Total Invested, but only summed over properties that have an
    # estimated_value (i.e. the same scope as total_estimated_profit) - the
    # correct denominator for estimated_profit_pct, since including
    # properties with no estimate in the denominator would understate the %.
    total_invested_with_estimate: float = 0.0
    properties_with_arv: int = 0
    properties_with_estimated_value: int = 0
    # Portfolio-wide cash-on-cash yield: sum of every property's annualized
    # net cash flow divided by sum of total invested (dollar-weighted, not
    # a plain average of each property's percentage).
    total_annual_net_cash_flow: float = 0.0
    total_invested_with_data: float = 0.0
    yield_pct: Optional[float] = None

    @property
    def yield_display(self) -> str:
        return f"{self.yield_pct:.2f}%" if self.yield_pct is not None else INSUFFICIENT_DATA

    @property
    def monthly_cash_flow(self) -> Optional[float]:
        return (self.total_annual_net_cash_flow / 12) if self.total_invested_with_data > 0 else None

    @property
    def estimated_profit_pct(self) -> Optional[float]:
        if self.total_invested_with_estimate <= 0:
            return None
        return (self.total_estimated_profit / self.total_invested_with_estimate) * 100


def get_portfolio_summary(properties: List[Property]) -> PortfolioSummary:
    summary = PortfolioSummary(property_count=len(properties))
    for prop in properties:
        summary.total_purchase_price += prop.total_purchase_price
        summary.total_renovation_costs += prop.total_renovation_costs
        summary.total_invested += prop.total_invested
        if prop.is_occupied:
            summary.occupied_count += 1
        if prop.max_arv is not None:
            summary.total_max_arv += float(prop.max_arv)
            summary.properties_with_arv += 1
        if prop.estimated_value is not None:
            summary.total_estimated_value += float(prop.estimated_value)
            summary.properties_with_estimated_value += 1
            summary.total_estimated_profit += prop.estimated_profit
            summary.total_invested_with_estimate += prop.total_invested

        py = get_property_yield(prop)
        if py.yield_pct is not None:
            summary.total_annual_net_cash_flow += py.annual_net_cash_flow
            summary.total_invested_with_data += py.total_invested

    if summary.total_invested_with_data > 0:
        summary.yield_pct = (summary.total_annual_net_cash_flow / summary.total_invested_with_data) * 100

    return summary


def get_properties(db: Session) -> List[Property]:
    return db.query(Property).order_by(Property.address).all()

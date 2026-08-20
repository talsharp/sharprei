import enum
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ZipStatus(str, enum.Enum):
    not_tried = "not_tried"
    active = "active"
    watchlist = "watchlist"
    blacklist = "blacklist"


class RunType(str, enum.Enum):
    initial = "initial"
    follow_up = "follow_up"


# Sentinel used for CampaignRun.run_date when the real date is not known (e.g.
# a bulk import with no date column and no default date given). Never treat
# this as a real date - always display "--" and exclude it from date math.
UNSET_DATE = date(1900, 1, 1)


class Neighborhood(Base):
    __tablename__ = "neighborhoods"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    region: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)

    zip_links: Mapped[list["ZipNeighborhood"]] = relationship(
        back_populates="neighborhood", cascade="all, delete-orphan"
    )


class ZipCode(Base):
    __tablename__ = "zip_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    zip_code: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    county: Mapped[str] = mapped_column(String(80), default="Allegheny County")
    status: Mapped[ZipStatus] = mapped_column(
        String(20), default=ZipStatus.not_tried
    )
    follow_up_status: Mapped[ZipStatus] = mapped_column(
        String(20), default=ZipStatus.not_tried
    )
    region: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    region_override: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    tier_override: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    neighborhood_links: Mapped[list["ZipNeighborhood"]] = relationship(
        back_populates="zip_code", cascade="all, delete-orphan"
    )
    runs: Mapped[list["CampaignRun"]] = relationship(
        back_populates="zip_code", cascade="all, delete-orphan"
    )

    @property
    def neighborhoods(self):
        return [link.neighborhood for link in self.neighborhood_links]

    @property
    def primary_neighborhood_link(self):
        return next((link for link in self.neighborhood_links if link.is_primary), None)

    @property
    def secondary_neighborhood_links(self):
        return [link for link in self.neighborhood_links if not link.is_primary]


class ZipNeighborhood(Base):
    __tablename__ = "zip_neighborhoods"
    __table_args__ = (UniqueConstraint("zip_code_id", "neighborhood_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    zip_code_id: Mapped[int] = mapped_column(ForeignKey("zip_codes.id"))
    neighborhood_id: Mapped[int] = mapped_column(ForeignKey("neighborhoods.id"))
    is_primary: Mapped[bool] = mapped_column(default=False)
    overlap_ratio: Mapped[Optional[float]] = mapped_column(nullable=True)

    zip_code: Mapped[ZipCode] = relationship(back_populates="neighborhood_links")
    neighborhood: Mapped[Neighborhood] = relationship(back_populates="zip_links")


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    row_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20), default="success")

    runs: Mapped[list["CampaignRun"]] = relationship(back_populates="import_batch")


class CampaignRun(Base):
    __tablename__ = "campaign_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    zip_code_id: Mapped[int] = mapped_column(ForeignKey("zip_codes.id"))
    run_date: Mapped[date] = mapped_column(Date)
    run_type: Mapped[RunType] = mapped_column(String(20), default=RunType.initial)
    sms_sent: Mapped[int] = mapped_column(default=0)
    replies: Mapped[int] = mapped_column(default=0)
    leads: Mapped[int] = mapped_column(default=0)
    warm: Mapped[int] = mapped_column(default=0)
    drip: Mapped[int] = mapped_column(default=0)
    signed_agreements: Mapped[int] = mapped_column(default=0)
    opt_out: Mapped[Optional[int]] = mapped_column(nullable=True)
    import_batch_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("import_batches.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    zip_code: Mapped[ZipCode] = relationship(back_populates="runs")
    import_batch: Mapped[Optional[ImportBatch]] = relationship(back_populates="runs")
    deals: Mapped[list["Deal"]] = relationship(
        back_populates="campaign_run", cascade="all, delete-orphan"
    )


class Deal(Base):
    __tablename__ = "deals"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_run_id: Mapped[int] = mapped_column(ForeignKey("campaign_runs.id"))
    profit: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    closed_date: Mapped[date] = mapped_column(Date)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    campaign_run: Mapped[CampaignRun] = relationship(back_populates="deals")


class Property(Base):
    __tablename__ = "properties"

    id: Mapped[int] = mapped_column(primary_key=True)
    address: Mapped[str] = mapped_column(String(255))
    purchase_price: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    closing_costs: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    purchase_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    max_arv: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    estimated_value: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    rent_price: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    tenant_move_in_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    account_balance: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    renovation_expenses: Mapped[list["RenovationExpense"]] = relationship(
        back_populates="property_ref", cascade="all, delete-orphan"
    )
    monthly_expenses: Mapped[list["MonthlyExpense"]] = relationship(
        back_populates="property_ref", cascade="all, delete-orphan"
    )

    @property
    def total_purchase_price(self) -> float:
        return float(self.purchase_price) + float(self.closing_costs)

    @property
    def total_renovation_costs(self) -> float:
        return sum(float(r.cost) for r in self.renovation_expenses)

    @property
    def total_invested(self) -> float:
        return self.total_purchase_price + self.total_renovation_costs

    @property
    def is_occupied(self) -> bool:
        return self.rent_price is not None and float(self.rent_price) > 0

    @property
    def estimated_profit(self) -> Optional[float]:
        if self.estimated_value is None:
            return None
        return float(self.estimated_value) - self.total_invested


class RenovationExpense(Base):
    __tablename__ = "renovation_expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"))
    description: Mapped[str] = mapped_column(String(255))
    cost: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    expense_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    contractor: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    file_original_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    property_ref: Mapped[Property] = relationship(back_populates="renovation_expenses")


class MonthlyExpense(Base):
    __tablename__ = "monthly_expenses"
    __table_args__ = (UniqueConstraint("property_id", "month"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"))
    month: Mapped[date] = mapped_column(Date)
    income: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    utilities: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    insurance: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    repairs: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    management_fees: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    property_tax: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    other: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    property_ref: Mapped[Property] = relationship(back_populates="monthly_expenses")

    @property
    def total_expenses(self) -> float:
        return (
            float(self.utilities)
            + float(self.insurance)
            + float(self.repairs)
            + float(self.management_fees)
            + float(self.property_tax)
            + float(self.other)
        )

    @property
    def net_cash_flow(self) -> float:
        return float(self.income) - self.total_expenses

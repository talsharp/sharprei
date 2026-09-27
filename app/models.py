import enum
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
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
    # Prepaids paid at closing - kept out of closing_costs so All-In Cost is
    # comparable between properties bought at different times of year.
    taxes_at_closing: Mapped[float] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    insurance_at_closing: Mapped[float] = mapped_column(Numeric(12, 2), default=0, server_default="0")
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
    owners: Mapped[list["PropertyOwner"]] = relationship(
        back_populates="property_ref", cascade="all, delete-orphan", order_by="PropertyOwner.id"
    )
    files: Mapped[list["PropertyFile"]] = relationship(
        back_populates="property_ref", cascade="all, delete-orphan", order_by="PropertyFile.uploaded_at.desc()"
    )

    @property
    def total_owner_percentage(self) -> float:
        return sum(float(o.percentage) for o in self.owners)

    @property
    def total_purchase_price(self) -> float:
        return float(self.purchase_price) + float(self.closing_costs)

    @property
    def upfront_costs(self) -> float:
        return float(self.taxes_at_closing or 0) + float(self.insurance_at_closing or 0)

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

    @property
    def estimated_profit_pct(self) -> Optional[float]:
        if self.estimated_profit is None or self.total_invested <= 0:
            return None
        return (self.estimated_profit / self.total_invested) * 100


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


class PropertyOwner(Base):
    __tablename__ = "property_owners"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"))
    name: Mapped[str] = mapped_column(String(150))
    percentage: Mapped[float] = mapped_column(Numeric(5, 2), default=0)

    property_ref: Mapped[Property] = relationship(back_populates="owners")


class MonthlyExpense(Base):
    __tablename__ = "monthly_expenses"
    __table_args__ = (UniqueConstraint("property_id", "month"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"))
    month: Mapped[date] = mapped_column(Date)
    income: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    utilities: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    utilities_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    insurance: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    insurance_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    repairs: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    repairs_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    management_fees: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    management_fees_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    property_tax: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    property_tax_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    other: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    other_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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


class Vendor(Base):
    __tablename__ = "finance_vendors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    default_channel: Mapped[str] = mapped_column(String(20), default="shared")
    recurring: Mapped[bool] = mapped_column(Boolean, default=False)

    expenses: Mapped[list["FinanceExpense"]] = relationship(back_populates="vendor")


class FinanceMonth(Base):
    __tablename__ = "finance_months"

    id: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[date] = mapped_column(Date, unique=True)
    sms_leads: Mapped[int] = mapped_column(default=0)
    sms_follow_up_leads: Mapped[int] = mapped_column(default=0)
    cold_call_leads: Mapped[int] = mapped_column(default=0)
    facebook_leads: Mapped[int] = mapped_column(default=0)

    expenses: Mapped[list["FinanceExpense"]] = relationship(
        back_populates="finance_month", cascade="all, delete-orphan"
    )

    @property
    def total_leads(self) -> int:
        return self.sms_leads + self.sms_follow_up_leads + self.cold_call_leads + self.facebook_leads


class FinanceExpense(Base):
    __tablename__ = "finance_expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    finance_month_id: Mapped[int] = mapped_column(ForeignKey("finance_months.id"))
    vendor_id: Mapped[int] = mapped_column(ForeignKey("finance_vendors.id"))
    amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    channel: Mapped[str] = mapped_column(String(20))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    finance_month: Mapped[FinanceMonth] = relationship(back_populates="expenses")
    vendor: Mapped[Vendor] = relationship(back_populates="expenses")


class WholesaleDeal(Base):
    __tablename__ = "wholesale_deals"

    id: Mapped[int] = mapped_column(primary_key=True)
    address: Mapped[str] = mapped_column(String(255))
    agreement_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    agreement_price: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    sell_price: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="under_contract")
    closed_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    revenue: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    realtor_commission: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    acquisition_commission: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    closing_costs: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    additional_costs: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    # For deals kept for the portfolio: the estimated profit it would have made
    # as a wholesale deal. Counted as that deal's wholesale profit (marked est.).
    estimated_value: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    channel: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    zip_code_id: Mapped[Optional[int]] = mapped_column(ForeignKey("zip_codes.id"), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    zip_code: Mapped[Optional[ZipCode]] = relationship()
    files: Mapped[list["DealFile"]] = relationship(
        back_populates="deal", cascade="all, delete-orphan", order_by="DealFile.uploaded_at.desc()"
    )

    @property
    def total_costs(self) -> float:
        return (
            float(self.realtor_commission or 0)
            + float(self.acquisition_commission or 0)
            + float(self.closing_costs or 0)
            + float(self.additional_costs or 0)
        )

    @property
    def profit(self) -> Optional[float]:
        if self.status == "closed":
            return float(self.revenue or 0) - self.total_costs
        if self.status == "kept":
            return float(self.estimated_value or 0)
        return None


class PropertyFile(Base):
    __tablename__ = "property_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(default=0)
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    property_ref: Mapped["Property"] = relationship(back_populates="files")


class DealFile(Base):
    __tablename__ = "deal_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("wholesale_deals.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(default=0)
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    deal: Mapped[WholesaleDeal] = relationship(back_populates="files")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="owner")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    user_email: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(20))
    table_name: Mapped[str] = mapped_column(String(64))
    record_id: Mapped[Optional[int]] = mapped_column(nullable=True)
    label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    changes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


# Registers the audit-log listener wherever the models are used (web app and scripts alike).
from app import audit  # noqa: E402,F401

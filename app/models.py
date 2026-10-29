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
    # Still sending (the VA's "Month Finished" = ONGOING). run_date is the
    # finished month once there is one; while ongoing the zip is on Watchlist.
    ongoing: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
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
    # Utility providers (the tenant pays them; kept for reference).
    electric_company: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    electric_account: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    gas_company: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    gas_account: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    water_company: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    water_account: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
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
    # "manual", "recurring_copy" (pre-filled from last month - replaced when a
    # card statement brings the real amount) or "statement".
    source: Mapped[str] = mapped_column(String(20), default="manual", server_default="manual")
    statement_import_id: Mapped[Optional[int]] = mapped_column(ForeignKey("statement_imports.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    finance_month: Mapped[FinanceMonth] = relationship(back_populates="expenses")
    statement_import: Mapped[Optional["StatementImport"]] = relationship()
    vendor: Mapped[Vendor] = relationship(back_populates="expenses")


class MatchRule(Base):
    """Recognizes card-statement charges: if `pattern` appears in the merchant
    name, the charge goes to `vendor_id` (on `channel`, or the vendor's default),
    or - with action "skip" - is shown but never added (e.g. personal, or the
    Google Ads for the shop)."""

    __tablename__ = "match_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    pattern: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(10), default="vendor")
    vendor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("finance_vendors.id"), nullable=True)
    channel: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    label: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    vendor: Mapped[Optional[Vendor]] = relationship()


class StatementImport(Base):
    __tablename__ = "statement_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255))
    statement_month: Mapped[date] = mapped_column(Date)
    ils_per_usd: Mapped[float] = mapped_column(Numeric(8, 4))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    committed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    charges: Mapped[list["StatementCharge"]] = relationship(
        back_populates="statement_import", cascade="all, delete-orphan", order_by="StatementCharge.row_index"
    )


class StatementCharge(Base):
    __tablename__ = "statement_charges"

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_import_id: Mapped[int] = mapped_column(ForeignKey("statement_imports.id"), index=True)
    row_index: Mapped[int] = mapped_column(default=0)
    card: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    txn_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    merchant: Mapped[str] = mapped_column(String(255))
    txn_amount: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    charged_amount: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    # "usd" (foreign charge - txn_amount is dollars), "ils", "ils_installment", "usd_billed", "other"
    kind: Mapped[str] = mapped_column(String(20))
    usd_amount: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    decision: Mapped[str] = mapped_column(String(10), default="new")  # "vendor", "skip", "new"
    vendor_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    channel: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    split_months: Mapped[int] = mapped_column(default=1)
    matched_rule_id: Mapped[Optional[int]] = mapped_column(ForeignKey("match_rules.id"), nullable=True)
    duplicate_of_import_id: Mapped[Optional[int]] = mapped_column(nullable=True)

    statement_import: Mapped[StatementImport] = relationship(back_populates="charges")


class WholesaleDeal(Base):
    __tablename__ = "wholesale_deals"

    id: Mapped[int] = mapped_column(primary_key=True)
    address: Mapped[str] = mapped_column(String(255))
    agreement_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    agreement_price: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)  # the 1st agreed price
    # Lower price after renegotiating (None = no renegotiation). The difference
    # is money made from the renegotiation.
    renegotiated_price: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
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
    # The property's zip. For SMS deals this (with sms_source) is what Zip Codes
    # counts: every deal is a signed agreement; closed/kept ones are closed deals.
    zip_code_id: Mapped[Optional[int]] = mapped_column(ForeignKey("zip_codes.id"), nullable=True)
    # Which SMS round the lead came from: first_text / follow_up_1 / _2 / _3.
    sms_source: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Inspection period in business days, counted from the agreement date.
    inspection_days: Mapped[Optional[int]] = mapped_column(nullable=True)
    # The end buyer once one is found. Their price is sell_price; closed_date
    # is the scheduled closing date until the deal closes.
    buyer_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    zip_code: Mapped[Optional[ZipCode]] = relationship()
    tasks: Mapped[list["DealTask"]] = relationship(
        back_populates="deal", cascade="all, delete-orphan",
        order_by="(DealTask.status == 'done', DealTask.due_date.is_(None), DealTask.due_date, DealTask.id)",
    )
    buyers: Mapped[list["DealBuyer"]] = relationship(
        back_populates="deal", cascade="all, delete-orphan",
        order_by="(DealBuyer.visit_at.is_(None), DealBuyer.visit_at, DealBuyer.id)",
    )
    files: Mapped[list["DealFile"]] = relationship(
        back_populates="deal", cascade="all, delete-orphan", order_by="DealFile.uploaded_at.desc()"
    )

    @property
    def final_price(self) -> Optional[float]:
        """What Tal pays in the end: the renegotiated price if there was one."""
        if self.renegotiated_price is not None:
            return float(self.renegotiated_price)
        return float(self.agreement_price) if self.agreement_price is not None else None

    @property
    def renegotiation_savings(self) -> Optional[float]:
        if self.renegotiated_price is None or self.agreement_price is None:
            return None
        return float(self.agreement_price) - float(self.renegotiated_price)

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


class TaskStatus(Base):
    """A status option on the Tasks board (Working on it, Done...). Tal manages the list."""
    __tablename__ = "task_statuses"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    color: Mapped[str] = mapped_column(String(9), default="#C4C4C4")
    position: Mapped[int] = mapped_column(default=0)
    # Tasks with this status look finished and are removed by "Clear done".
    is_done: Mapped[bool] = mapped_column(Boolean, default=False)


class TaskLabel(Base):
    """What a task relates to (Dispo, Acquisition, General...). Tal manages the list."""
    __tablename__ = "task_labels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    color: Mapped[str] = mapped_column(String(9), default="#579BFC")
    position: Mapped[int] = mapped_column(default=0)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Fixed weekday groups, not dates: "sun".."sat" or "later".
    day: Mapped[str] = mapped_column(String(10), default="later", index=True)
    position: Mapped[int] = mapped_column(default=0)
    status_id: Mapped[Optional[int]] = mapped_column(ForeignKey("task_statuses.id", ondelete="SET NULL"), nullable=True)
    label_id: Mapped[Optional[int]] = mapped_column(ForeignKey("task_labels.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    status: Mapped[Optional[TaskStatus]] = relationship()
    label: Mapped[Optional[TaskLabel]] = relationship()


class CompAnalysis(Base):
    """Tools › Comps: one house being valued ("my house") and the comps for it."""
    __tablename__ = "comp_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    arv_low: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    arv_avg: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    arv_high: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Read-only public page at /c/<token>; None = link turned off.
    share_token: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, unique=True, index=True)
    saved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # None = still a draft
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    houses: Mapped[list["CompHouse"]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan",
        order_by="(CompHouse.is_subject.desc(), CompHouse.position, CompHouse.id)",
    )

    @property
    def subject(self) -> Optional["CompHouse"]:
        return next((h for h in self.houses if h.is_subject), None)

    @property
    def comps(self) -> list["CompHouse"]:
        return [h for h in self.houses if not h.is_subject]


class CompHouse(Base):
    """A house in a comp analysis - the subject ("my house") or one comp."""
    __tablename__ = "comp_houses"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("comp_analyses.id"), index=True)
    is_subject: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(default=0)
    address: Mapped[str] = mapped_column(String(255))
    zillow_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    bedrooms: Mapped[Optional[float]] = mapped_column(Numeric(4, 1), nullable=True)
    bathrooms: Mapped[Optional[float]] = mapped_column(Numeric(4, 1), nullable=True)
    sqft: Mapped[Optional[int]] = mapped_column(nullable=True)
    year_built: Mapped[Optional[int]] = mapped_column(nullable=True)
    sold_price: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    sold_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    listed_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    # Used only when the listed or sold date is unknown; otherwise days on
    # market is sold date - listed date.
    days_on_market_manual: Mapped[Optional[int]] = mapped_column(nullable=True)
    pros: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cons: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    lookup_note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    analysis: Mapped[CompAnalysis] = relationship(back_populates="houses")

    @property
    def days_on_market(self) -> Optional[int]:
        if self.listed_date and self.sold_date and self.sold_date >= self.listed_date:
            return (self.sold_date - self.listed_date).days
        return self.days_on_market_manual


class ApiUsage(Base):
    """Paid API calls per month (e.g. RentCast lookups), to keep an eye on cost."""
    __tablename__ = "api_usage"
    __table_args__ = (UniqueConstraint("service", "month"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    service: Mapped[str] = mapped_column(String(40))
    month: Mapped[date] = mapped_column(Date)
    calls: Mapped[int] = mapped_column(default=0)


class DealBuyer(Base):
    """An investor Tal is trying to sell an under-contract deal to, and their walkthrough."""
    __tablename__ = "deal_buyers"

    id: Mapped[int] = mapped_column(primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("wholesale_deals.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    # Each buyer has their own visit time: same time = a group walkthrough,
    # different times = a second walkthrough or a vacant house.
    visit_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="waiting")  # waiting / confirmed / visited / cant
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    deal: Mapped["WholesaleDeal"] = relationship(back_populates="buyers")


class DealPage(Base):
    """A public landing page for a deal (like an InvestorBase listing), shared by link."""
    __tablename__ = "deal_pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("wholesale_deals.id"), unique=True)
    token: Mapped[str] = mapped_column(String(40), unique=True, index=True)  # the general link
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    title: Mapped[str] = mapped_column(String(255))
    asking_price: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    property_type: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    bedrooms: Mapped[Optional[float]] = mapped_column(Numeric(4, 1), nullable=True)
    bathrooms: Mapped[Optional[float]] = mapped_column(Numeric(4, 1), nullable=True)
    sqft: Mapped[Optional[int]] = mapped_column(nullable=True)
    year_built: Mapped[Optional[int]] = mapped_column(nullable=True)
    arv: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    rent: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    walkthrough: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contact_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    contact_photo: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # stored file name
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    deal: Mapped["WholesaleDeal"] = relationship()
    photos: Mapped[list["DealPhoto"]] = relationship(
        back_populates="page", cascade="all, delete-orphan", order_by="(DealPhoto.position, DealPhoto.id)"
    )
    links: Mapped[list["DealPageLink"]] = relationship(
        back_populates="page", cascade="all, delete-orphan", order_by="DealPageLink.id"
    )
    views: Mapped[list["DealPageView"]] = relationship(
        back_populates="page", cascade="all, delete-orphan", order_by="DealPageView.created_at.desc()"
    )


class DealPhoto(Base):
    __tablename__ = "deal_photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("deal_pages.id"), index=True)
    stored_name: Mapped[str] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    page: Mapped[DealPage] = relationship(back_populates="photos")


class DealPageLink(Base):
    """A personal link for one investor, so the views show who looked."""
    __tablename__ = "deal_page_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("deal_pages.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    token: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    page: Mapped[DealPage] = relationship(back_populates="links")
    views: Mapped[list["DealPageView"]] = relationship(back_populates="link", order_by="DealPageView.created_at.desc()")


class DealPageView(Base):
    """One visit to a deal page. visitor_id is an anonymous browser cookie, so a
    later visit from the same browser counts as returning."""
    __tablename__ = "deal_page_views"

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("deal_pages.id"), index=True)
    link_id: Mapped[Optional[int]] = mapped_column(ForeignKey("deal_page_links.id", ondelete="SET NULL"), nullable=True)
    visitor_id: Mapped[str] = mapped_column(String(40))
    is_return: Mapped[bool] = mapped_column(Boolean, default=False)
    device: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # How many different photos they opened in the photo viewer (0 = didn't open it).
    photos_seen: Mapped[int] = mapped_column(default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    page: Mapped[DealPage] = relationship(back_populates="views")
    link: Mapped[Optional[DealPageLink]] = relationship(back_populates="views")


class DealTask(Base):
    """A to-do for a deal (send to title, collect EMD...). Due today or overdue
    and not done = shown as a reminder on the Deals page and in the sidebar."""
    __tablename__ = "deal_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("wholesale_deals.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="todo")  # todo / in_progress / waiting / done
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    deal: Mapped["WholesaleDeal"] = relationship(back_populates="tasks")


class NotificationDismissal(Base):
    """A bell reminder Tal ticked off. The key includes the day, so a reminder
    hidden today can come back tomorrow if it's still relevant."""
    __tablename__ = "notification_dismissals"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Project(Base):
    """A deal outside the rental portfolio (e.g. Warner Robins with Gal & Ran):
    a money ledger for the accountant plus the loan terms of the agreement."""
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    properties: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    partners: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    loan_rate: Mapped[Optional[float]] = mapped_column(Numeric(6, 3), nullable=True)  # % a year, e.g. 4.5
    loan_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    payments: Mapped[list["ProjectPayment"]] = relationship(
        back_populates="project", cascade="all, delete-orphan",
        order_by="(ProjectPayment.paid_on, ProjectPayment.id)")


class ProjectPayment(Base):
    """One money movement in a project. `kind` decides how it counts toward
    the loan (see app/projects.py); `netted` = no money actually moved, it was
    taken off another payment."""
    __tablename__ = "project_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    paid_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    kind: Mapped[str] = mapped_column(String(20), default="other")
    paid_by: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    paid_to: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    account: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    needs_recheck: Mapped[bool] = mapped_column(Boolean, default=False)
    netted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped[Project] = relationship(back_populates="payments")


# Registers the audit-log listener wherever the models are used (web app and scripts alike).
from app import audit  # noqa: E402,F401

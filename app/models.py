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


class Neighborhood(Base):
    __tablename__ = "neighborhoods"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)

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
    avg_house_value: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
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


class ZipNeighborhood(Base):
    __tablename__ = "zip_neighborhoods"
    __table_args__ = (UniqueConstraint("zip_code_id", "neighborhood_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    zip_code_id: Mapped[int] = mapped_column(ForeignKey("zip_codes.id"))
    neighborhood_id: Mapped[int] = mapped_column(ForeignKey("neighborhoods.id"))

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

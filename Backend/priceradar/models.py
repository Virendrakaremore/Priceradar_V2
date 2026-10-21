"""Relational schema. Prices are Numeric (Decimal), timestamps are UTC."""
from __future__ import annotations

import warnings
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text,
                        UniqueConstraint, create_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.types import TypeDecorator

from .timeutil import as_utc, utcnow

warnings.filterwarnings("ignore", message="Dialect sqlite\\+pysqlite does \\*not\\* support Decimal")


class UTCDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return as_utc(value).replace(tzinfo=None) if value else None

    def process_result_value(self, value, dialect):
        return as_utc(value) if value else None


Money = Numeric(12, 2, asdecimal=True)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    notify_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    default_cooldown_s: Mapped[int] = mapped_column(Integer, default=3600)


class Retailer(Base):
    __tablename__ = "retailers"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    domain: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    min_interval_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    live_verified: Mapped[bool] = mapped_column(Boolean, default=False)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    brand: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(128))
    gtin: Mapped[str | None] = mapped_column(String(32), index=True)
    image_url: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    offers: Mapped[list["Offer"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class Offer(Base):
    """One product listing at one retailer (the unit that is monitored)."""
    __tablename__ = "offers"
    __table_args__ = (UniqueConstraint("canonical_url", name="uq_offer_canonical_url"),
                      Index("ix_offer_due", "monitoring_enabled", "next_check_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    retailer_id: Mapped[int] = mapped_column(ForeignKey("retailers.id"), index=True)
    canonical_url: Mapped[str] = mapped_column(String(1024))
    original_url: Mapped[str | None] = mapped_column(String(1024))
    external_id: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str | None] = mapped_column(String(512))
    image_url: Mapped[str | None] = mapped_column(Text)
    variant_label: Mapped[str | None] = mapped_column(String(256))
    # source = the URL the user pasted; verified = matched with evidence; possible = alternative only
    match_status: Mapped[str] = mapped_column(String(16), default="source")
    match_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    match_reasons: Mapped[list] = mapped_column(JSON, default=list)

    current_price: Mapped[Decimal | None] = mapped_column(Money)
    mrp: Mapped[Decimal | None] = mapped_column(Money)
    delivery_charge: Mapped[Decimal | None] = mapped_column(Money)
    availability: Mapped[str] = mapped_column(String(16), default="unknown")  # in_stock/out_of_stock/unknown
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    # ok | stale | unavailable | blocked | error | pending
    status: Mapped[str] = mapped_column(String(16), default="pending")
    last_error: Mapped[str | None] = mapped_column(String(512))
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_change_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    pending_price: Mapped[Decimal | None] = mapped_column(Money)  # awaiting confirmation of a big jump

    monitoring_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    check_interval_s: Mapped[int] = mapped_column(Integer, default=15)
    next_check_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    circuit_open_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    product: Mapped[Product] = relationship(back_populates="offers")
    retailer: Mapped[Retailer] = relationship()


class PriceObservation(Base):
    __tablename__ = "price_observations"
    __table_args__ = (Index("ix_obs_offer_time", "offer_id", "observed_at"),
                      Index("ix_obs_product_time", "product_id", "observed_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    retailer_id: Mapped[int] = mapped_column(ForeignKey("retailers.id"))
    variant_label: Mapped[str | None] = mapped_column(String(256))
    price: Mapped[Decimal] = mapped_column(Money)
    mrp: Mapped[Decimal | None] = mapped_column(Money)
    availability: Mapped[str] = mapped_column(String(16), default="unknown")
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="verified")
    source: Mapped[str] = mapped_column(String(32), default="scrape")  # scrape|jsonld|api|import
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "product_id", name="uq_sub_user_product"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    track_until: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    product: Mapped[Product] = relationship()


ALERT_KINDS = ("target_reached", "pct_drop", "amount_drop", "any_decrease", "any_increase",
               "historical_low", "back_in_stock", "flash_drop", "lower_elsewhere")


class AlertRule(Base):
    __tablename__ = "alert_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("offers.id", ondelete="CASCADE"))  # None = any offer
    kind: Mapped[str] = mapped_column(String(24))
    target_price: Mapped[Decimal | None] = mapped_column(Money)
    pct_threshold: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    amount_threshold: Mapped[Decimal | None] = mapped_column(Money)
    min_change: Mapped[Decimal | None] = mapped_column(Money)
    cooldown_s: Mapped[int] = mapped_column(Integer, default=3600)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # ``armed`` re-arms threshold rules once the condition becomes false again (prevents repeat mails)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    last_fired_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class NotificationEvent(Base):
    __tablename__ = "notification_events"
    __table_args__ = (UniqueConstraint("rule_id", "event_key", name="uq_event_dedupe"),
                      Index("ix_notif_status", "status", "next_attempt_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("alert_rules.id", ondelete="SET NULL"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("offers.id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(24))
    event_key: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    recipient: Mapped[str] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|sent|failed|dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(512))
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ScrapeFailure(Base):
    __tablename__ = "scrape_failures"
    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("offers.id", ondelete="CASCADE"), index=True)
    retailer_id: Mapped[int | None] = mapped_column(ForeignKey("retailers.id"))
    category: Mapped[str] = mapped_column(String(24))  # network|timeout|blocked|parse|not_found|unsupported
    message: Mapped[str] = mapped_column(String(512))
    retryable: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class MatchCandidate(Base):
    __tablename__ = "match_candidates"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    retailer_key: Mapped[str] = mapped_column(String(32))
    url: Mapped[str] = mapped_column(String(1024))
    title: Mapped[str | None] = mapped_column(String(512))
    score: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    status: Mapped[str] = mapped_column(String(16))  # verified|possible|rejected
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    provider: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Job(Base):
    """Product-identification job with real, observable stages."""
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), default="identify")
    input_url: Mapped[str] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed
    stage: Mapped[str] = mapped_column(String(32), default="queued")
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(String(512))
    error_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_beat: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)


def make_engine(url: str):
    kwargs = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _):  # WAL lets the worker write while the API reads
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
    return engine


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)

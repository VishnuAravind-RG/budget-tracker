from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import declarative_base

from timeutil import utc_now_naive

Base = declarative_base()

# Only "expense" is real spending. topup (wallet load) and transfer (own
# accounts) aren't spending at all; lend/repayment track money between people
# separately from both. Getting this wrong is the single biggest source of
# wrong totals in a naive tracker — see budget_summary()'s filter in main.py.
KINDS = ("expense", "income", "transfer", "topup", "lend", "repayment")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    raw_text = Column(String, nullable=True)          # original SMS, if from SMS
    merchant = Column(String, nullable=True)
    amount = Column(Float, nullable=False)
    direction = Column(String, nullable=False)         # "debit" or "credit"
    category = Column(String, nullable=False, default="Uncategorized")
    source = Column(String, nullable=False, default="manual")  # "sms" | "manual"
    needs_review = Column(Boolean, nullable=False, default=False)  # AI unsure -> user categorizes
    # Always stored as naive UTC so SQLite and Postgres behave identically.
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)

    # Added after the table already existed in production — db.py's
    # _ensure_columns() ALTERs the live table for these, since
    # Base.metadata.create_all() only creates missing tables, never adds
    # columns to ones that already exist.
    kind = Column(String, nullable=False, default="expense")
    payee_key = Column(String, nullable=True, index=True)  # UPI id, or "name:<merchant>"
    counterparty = Column(String, nullable=True)  # person's display name, for lend/repayment

    __table_args__ = (
        Index("ix_transactions_created_at", "created_at"),
    )


class Budget(Base):
    __tablename__ = "budgets"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String, nullable=False, unique=True)
    monthly_limit = Column(Float, nullable=False)


class Payee(Base):
    """Remembered answer to 'who is this?' — keyed by UPI id, or by a
    normalised merchant name for a card swipe with no VPA. Looked up once per
    ingest so the same counterparty is never asked about twice."""
    __tablename__ = "payees"

    key = Column(String, primary_key=True)
    label = Column(String, nullable=False)
    kind = Column(String, nullable=False)  # merchant | friend | wallet | self
    default_category = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)


class Vehicle(Base):
    __tablename__ = "vehicles"

    id = Column(String, primary_key=True)  # short slug: "activa", "speed400"
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)  # scooter | motorcycle | car
    fuel = Column(String, nullable=False, default="petrol")
    tank_capacity_l = Column(Float, nullable=True)
    archived = Column(Boolean, nullable=False, default=False)


class FuelFill(Base):
    """One fuel purchase. Mileage (km/L) is only derivable between two
    consecutive full-tank fills with odometer readings — see /fuel/mileage in
    main.py, which deliberately never fabricates a number from a partial
    fill or a backwards odometer reading."""
    __tablename__ = "fuel_fills"

    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(String, nullable=False, index=True)
    transaction_id = Column(Integer, nullable=True, index=True)
    amount = Column(Float, nullable=False)
    liters = Column(Float, nullable=True)
    odometer = Column(Float, nullable=True)
    is_full_tank = Column(Boolean, nullable=False, default=True)
    station = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)


class Todo(Base):
    __tablename__ = "todos"

    id = Column(Integer, primary_key=True, index=True)
    text = Column(String, nullable=False)
    done = Column(Boolean, nullable=False, default=False)
    order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    completed_at = Column(DateTime, nullable=True)


class GmailAuth(Base):
    """Single-row store (id is always 1) for the Gmail OAuth refresh token
    the poller uses, plus a watermark of when it last checked so a run only
    asks Gmail for messages newer than that instead of re-scanning the whole
    inbox every time. See gmail_poll.py."""
    __tablename__ = "gmail_auth"

    id = Column(Integer, primary_key=True, default=1)
    refresh_token = Column(String, nullable=False)
    last_poll_at = Column(DateTime, nullable=True)


class LendingReminder(Base):
    """'Nudge me again in N days to ask this person for the money back.'"""
    __tablename__ = "lending_reminders"

    person = Column(String, primary_key=True)
    next_reminder_at = Column(DateTime, nullable=False)
    snooze_days = Column(Integer, nullable=False, default=3)

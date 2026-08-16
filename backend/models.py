from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Index
from sqlalchemy.orm import declarative_base

from timeutil import utc_now_naive

Base = declarative_base()


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

    __table_args__ = (
        Index("ix_transactions_created_at", "created_at"),
    )


class Budget(Base):
    __tablename__ = "budgets"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String, nullable=False, unique=True)
    monthly_limit = Column(Float, nullable=False)

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from categorizer import CATEGORIES


# ---------- requests ----------

class SMSPayload(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ManualTransaction(BaseModel):
    amount: float = Field(gt=0)
    direction: str = "debit"
    category: str
    merchant: Optional[str] = Field(default=None, max_length=60)

    @field_validator("direction")
    @classmethod
    def _direction(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in ("debit", "credit"):
            raise ValueError("direction must be 'debit' or 'credit'")
        return v

    @field_validator("category")
    @classmethod
    def _category(cls, v: str) -> str:
        if v not in CATEGORIES:
            raise ValueError(f"category must be one of {CATEGORIES}")
        return v


class CategoryUpdate(BaseModel):
    category: str

    @field_validator("category")
    @classmethod
    def _category(cls, v: str) -> str:
        if v not in CATEGORIES:
            raise ValueError(f"category must be one of {CATEGORIES}")
        return v


class BudgetSet(BaseModel):
    category: str
    monthly_limit: float = Field(ge=0)

    @field_validator("category")
    @classmethod
    def _category(cls, v: str) -> str:
        if v not in CATEGORIES:
            raise ValueError(f"category must be one of {CATEGORIES}")
        return v


# ---------- responses ----------

class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    merchant: Optional[str]
    amount: float
    direction: str
    category: str
    source: str
    needs_review: bool
    raw_text: Optional[str]
    created_at: datetime

    @field_serializer("created_at")
    def _utc(self, dt: datetime) -> str:
        # Stored naive-UTC; tag it so the browser doesn't read it as local time.
        return dt.isoformat() + "Z"


class CategorySummary(BaseModel):
    category: str
    limit: Optional[float]
    spent: float
    remaining: Optional[float]
    percent_used: Optional[float]


class BudgetSummary(BaseModel):
    month: int
    year: int
    total_spent: float
    total_income: float
    total_budget: float
    categories: list[CategorySummary]


class DailyPoint(BaseModel):
    date: str
    spent: float


class TrendOut(BaseModel):
    month: int
    year: int
    days: list[DailyPoint]

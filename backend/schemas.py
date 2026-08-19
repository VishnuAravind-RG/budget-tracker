from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from categorizer import CATEGORIES


def _validate_category(v: str) -> str:
    if v not in CATEGORIES:
        raise ValueError(f"category must be one of {CATEGORIES}")
    return v


# ---------- requests ----------

class SMSPayload(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ManualTransaction(BaseModel):
    amount: float = Field(gt=0)
    direction: str = "debit"
    # Optional, not required: for kind="friend"/"wallet"/"self" the frontend
    # doesn't show a category picker at all (Lending/Transfer are applied
    # automatically) — a required field here would have forced the leftover
    # value from a hidden dropdown onto every lending entry.
    category: Optional[str] = None
    merchant: Optional[str] = Field(default=None, max_length=60)
    # What this actually is — same vocabulary as TransactionClassify, so a
    # manually-logged "sent to a friend" is tracked as lending (not spending)
    # exactly like an SMS-ingested one, not just categorised. "expense" (the
    # default) preserves the old plain-expense-or-income behaviour untouched.
    kind: str = "expense"
    counterparty: Optional[str] = Field(default=None, max_length=80)

    @field_validator("direction")
    @classmethod
    def _direction(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in ("debit", "credit"):
            raise ValueError("direction must be 'debit' or 'credit'")
        return v

    @field_validator("category")
    @classmethod
    def _category(cls, v: Optional[str]) -> Optional[str]:
        return _validate_category(v) if v is not None else v

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in ("expense", "friend", "wallet", "self"):
            raise ValueError("kind must be one of: expense, friend, wallet, self")
        return v


class MerchantUpdate(BaseModel):
    """Renames a transaction's merchant/note in place — e.g. filling in a
    location-resolved place name after the fact, without touching its
    category, kind, or review status the way /classify would."""

    merchant: str = Field(min_length=1, max_length=80)


class CategoryUpdate(BaseModel):
    category: str

    @field_validator("category")
    @classmethod
    def _category(cls, v: str) -> str:
        return _validate_category(v)


class TransactionClassify(BaseModel):
    """The 'who is this?' answer from the Review tab — richer than a plain
    category change. Setting `remember=True` (the default) also upserts a
    Payee row keyed by the transaction's payee_key, so this exact question is
    never asked again for the same counterparty."""

    kind: str
    category: Optional[str] = None
    label: Optional[str] = Field(default=None, max_length=80)
    remember: bool = True

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in ("expense", "friend", "wallet", "self"):
            # "friend"/"wallet"/"self" here map to lend-or-repayment/topup-or-
            # transfer/transfer depending on the transaction's direction —
            # resolved server-side in main.py, where direction is known.
            raise ValueError("kind must be one of: expense, friend, wallet, self")
        return v

    @field_validator("category")
    @classmethod
    def _category(cls, v: Optional[str]) -> Optional[str]:
        return _validate_category(v) if v is not None else v


class BudgetSet(BaseModel):
    category: str
    monthly_limit: float = Field(ge=0)

    @field_validator("category")
    @classmethod
    def _category(cls, v: str) -> str:
        return _validate_category(v)


class VehicleIn(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=60)
    type: str
    fuel: str = "petrol"
    tank_capacity_l: Optional[float] = Field(default=None, gt=0)

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in ("scooter", "motorcycle", "car"):
            raise ValueError("type must be scooter, motorcycle, or car")
        return v

    @field_validator("fuel")
    @classmethod
    def _fuel(cls, v: str) -> str:
        if v not in ("petrol", "diesel", "ev"):
            raise ValueError("fuel must be petrol, diesel, or ev")
        return v


class FuelFillIn(BaseModel):
    vehicle_id: str
    transaction_id: Optional[int] = None
    amount: float = Field(gt=0)
    liters: Optional[float] = Field(default=None, gt=0)
    odometer: Optional[float] = Field(default=None, ge=0)
    is_full_tank: bool = True
    station: Optional[str] = None


class TodoIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)


class TodoUpdate(BaseModel):
    text: Optional[str] = Field(default=None, min_length=1, max_length=300)
    done: Optional[bool] = None


class ImportItem(BaseModel):
    """One historical expense with no exact date — a spreadsheet only records
    which month it happened in. Booked to the 1st of that month at noon
    local time and excluded from the daily trend (see /stats/daily) so it
    doesn't draw a fake spike, while still counting toward that month's
    total via /budget/summary."""

    amount: float = Field(gt=0)
    category: str
    merchant: Optional[str] = Field(default=None, max_length=80)
    month: int = Field(ge=1, le=12)
    year: int = Field(ge=2000, le=2100)

    @field_validator("category")
    @classmethod
    def _category(cls, v: str) -> str:
        return _validate_category(v)


class ImportRequest(BaseModel):
    items: list[ImportItem] = Field(min_length=1, max_length=2000)
    # Explicit opt-in to re-run after an import already happened once —
    # prevents an accidental double-click from silently doubling every total.
    force: bool = False


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
    kind: str = "expense"
    payee_key: Optional[str] = None
    counterparty: Optional[str] = None

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


class VehicleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    type: str
    fuel: str
    tank_capacity_l: Optional[float]
    archived: bool


class FuelFillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vehicle_id: str
    transaction_id: Optional[int]
    amount: float
    liters: Optional[float]
    odometer: Optional[float]
    is_full_tank: bool
    station: Optional[str]
    created_at: datetime

    @field_serializer("created_at")
    def _utc(self, dt: datetime) -> str:
        return dt.isoformat() + "Z"


class MileageLeg(BaseModel):
    from_fill_id: int
    to_fill_id: int
    km: float
    liters: float
    km_per_liter: float
    cost_per_km: float


class MileageOut(BaseModel):
    vehicle_id: str
    total_spent: float
    total_liters: float
    avg_price_per_liter: Optional[float]
    avg_mileage: Optional[float]
    last_mileage: Optional[float]
    legs: list[MileageLeg]


class TodoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    text: str
    done: bool
    order: int
    created_at: datetime
    completed_at: Optional[datetime]

    @field_serializer("created_at", "completed_at")
    def _utc(self, dt: Optional[datetime]) -> Optional[str]:
        return dt.isoformat() + "Z" if dt else None


class ReceiptScanOut(BaseModel):
    """What Gemini read off the photo — a preview, not a booked transaction.

    The frontend shows this in the same shop/person/wallet/self chooser as a
    manual entry before anything is saved: a scanned screenshot of money sent
    to a friend must be classified as lending, not silently booked as a plain
    expense just because it came from a photo instead of a form.
    """

    amount: float
    merchant: Optional[str] = None
    direction: str
    category: str
    confident: bool


class LendingBalance(BaseModel):
    person: str
    lent: float
    repaid: float
    outstanding: float
    next_reminder_at: Optional[str] = None

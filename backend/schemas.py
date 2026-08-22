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


# A personal tracker will never legitimately see a fifteen-digit figure, but
# a fat-fingered extra zero is easy and would silently wreck every total and
# chart it appears in. One crore is far above any realistic personal expense
# while still catching that class of typo. Applies to hand entry only — an
# amount parsed from a bank's own alert is whatever the bank says it is.
MAX_MANUAL_AMOUNT = 10_000_000


class ManualTransaction(BaseModel):
    amount: float = Field(gt=0, le=MAX_MANUAL_AMOUNT)
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
    note: Optional[str] = Field(default=None, max_length=200)
    # Local calendar date (YYYY-MM-DD) the spend actually happened, for
    # logging something after the fact — cash from yesterday, or a payment a
    # bank never alerted about. Omitted means now.
    occurred_on: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

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
        if v not in ("expense", "friend", "friend_settle", "wallet", "self"):
            raise ValueError("kind must be one of: expense, friend, friend_settle, wallet, self")
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
    # What this actually was, in the user's own words. The UI asks for it when
    # the category is "Other", which by definition explains nothing.
    note: Optional[str] = Field(default=None, max_length=200)
    remember: bool = True

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in ("expense", "friend", "friend_settle", "wallet", "self"):
            # "friend"/"friend_settle"/"wallet"/"self" map to lend-or-
            # repayment/always-transfer/topup-or-transfer/transfer depending
            # on the transaction's direction — resolved server-side in
            # main.py, where direction is known.
            raise ValueError("kind must be one of: expense, friend, friend_settle, wallet, self")
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
    # Trip-meter distance since the last fill. Either this or `odometer` is
    # enough for mileage; trip_km wins when both are given (see fuel_mileage).
    trip_km: Optional[float] = Field(default=None, gt=0)
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
    note: Optional[str] = None
    bank_ref: Optional[str] = None
    import_batch: Optional[str] = None
    # Only ever populated on the review queue, where the answer is still
    # being asked for. Computed, never stored — see categorizer.business_hint().
    business_hint: Optional[str] = None

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
    trip_km: Optional[float] = None
    is_full_tank: bool
    station: Optional[str]
    created_at: datetime

    @field_serializer("created_at")
    def _utc(self, dt: datetime) -> str:
        return dt.isoformat() + "Z"


class MileageLeg(BaseModel):
    # None for a trip-meter leg: it's self-contained in the later fill, so
    # there is genuinely no earlier fill it was measured against.
    from_fill_id: Optional[int] = None
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


class PayeeOut(BaseModel):
    """One remembered 'who is this?' answer — see models.Payee. Read-only:
    the memory is written by /transactions/{id}/classify and add_manual(),
    this is just a way to actually see it, which nothing exposed before."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    kind: str
    default_category: Optional[str]
    created_at: datetime
    # Why this looks like a business, when it does — computed, not stored.
    # Surfaced against answers of "a person", which is where getting it wrong
    # is expensive: a shop remembered as a friend files every future payment
    # there as money lent out instead of spending, and is never asked about
    # again. See categorizer.business_hint().
    business_hint: Optional[str] = None
    # How many transactions this answer has already decided.
    used_by: int = 0

    @field_serializer("created_at")
    def _utc(self, dt: datetime) -> str:
        return dt.isoformat() + "Z"


class PayeeUpdate(BaseModel):
    """Correct a remembered answer that was wrong.

    Forgetting a payee (DELETE) only stops it being applied in future — it
    leaves the rows it already mis-filed alone, and there was no way at all
    to say "that was actually a shop". `apply_to_past` re-resolves every
    transaction the answer decided, which is the only way a bakery wrongly
    remembered as a friend stops appearing in the who-owes-you list.
    """

    kind: str
    label: Optional[str] = Field(default=None, max_length=80)
    category: Optional[str] = None
    apply_to_past: bool = False

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in ("expense", "friend", "friend_settle", "wallet", "self"):
            raise ValueError("kind must be one of: expense, friend, friend_settle, wallet, self")
        return v

    @field_validator("category")
    @classmethod
    def _category(cls, v: Optional[str]) -> Optional[str]:
        return _validate_category(v) if v is not None else v


class ScannedRow(BaseModel):
    """One row confirmed off a screenshot of a transaction list."""

    amount: float = Field(gt=0, le=MAX_MANUAL_AMOUNT)
    direction: str = "debit"
    category: Optional[str] = None
    merchant: Optional[str] = Field(default=None, max_length=60)
    kind: str = "expense"
    occurred_on: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

    @field_validator("direction")
    @classmethod
    def _direction(cls, v: str) -> str:
        if v not in ("debit", "credit"):
            raise ValueError("direction must be 'debit' or 'credit'")
        return v

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in ("expense", "friend", "friend_settle", "wallet", "self"):
            raise ValueError("kind must be one of: expense, friend, friend_settle, wallet, self")
        return v

    @field_validator("category")
    @classmethod
    def _category(cls, v: Optional[str]) -> Optional[str]:
        return _validate_category(v) if v is not None else v


class ScreenshotImport(BaseModel):
    """A whole screenshot's worth of confirmed rows, written as one batch.

    One request rather than one per row: the previous client looped over
    addManual(), so a six-row screenshot was six round trips to a
    single-worker free-tier backend, and a failure halfway left half a
    screenshot imported with no record of which half.
    """

    rows: list[ScannedRow] = Field(min_length=1, max_length=100)


class LendingBalance(BaseModel):
    person: str
    lent: float
    repaid: float
    outstanding: float
    next_reminder_at: Optional[str] = None

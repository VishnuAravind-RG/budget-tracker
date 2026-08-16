import os
from datetime import timedelta

from dotenv import load_dotenv

# Must run before any module that reads env vars at import time (auth, categorizer).
# utf-8-sig so a .env saved by a Windows editor (BOM) still parses.
load_dotenv(encoding="utf-8-sig")

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from sqlalchemy import func  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from auth import require_token  # noqa: E402
from categorizer import CATEGORIES, categorize, parse_sms  # noqa: E402
from db import get_db, init_db  # noqa: E402
from models import Budget, Transaction  # noqa: E402
from schemas import (  # noqa: E402
    BudgetSet,
    BudgetSummary,
    CategoryUpdate,
    ManualTransaction,
    SMSPayload,
    TransactionOut,
    TrendOut,
)
from timeutil import (  # noqa: E402
    days_in_month,
    local_day_key,
    local_now,
    month_range_utc,
    utc_now_naive,
)

app = FastAPI(title="Budget Tracker API")

# Auth is a bearer token in a header (no cookies), so "*" is safe by default.
# Set CORS_ORIGINS to your frontend URL once it's deployed to tighten it anyway.
origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()

# Everything on this router requires Authorization: Bearer <AUTH_TOKEN>.
api = APIRouter(dependencies=[Depends(require_token)])

DEDUPE_WINDOW = timedelta(seconds=120)


def _resolve_month(month: int | None, year: int | None) -> tuple[int, int]:
    now = local_now()
    return month or now.month, year or now.year


# ---------------------------------------------------------------- transactions

@api.post("/sms/ingest")
def ingest_sms(payload: SMSPayload, db: Session = Depends(get_db)):
    """Called by MacroDroid (or any SMS-forwarding automation) on every bank SMS."""
    return _ingest(payload.text, db)


@api.post("/sms/ingest/raw")
async def ingest_sms_raw(request: Request, db: Session = Depends(get_db)):
    """Same thing, but the whole request body *is* the SMS text.

    Use this if your automation can't escape quotes safely — an SMS containing a
    double quote would otherwise produce malformed JSON and a 422.
    """
    text = (await request.body()).decode("utf-8", errors="replace").strip()
    if not text:
        raise HTTPException(422, "Empty body")
    return _ingest(text[:2000], db)


def _ingest(text: str, db: Session):
    parsed = parse_sms(text)

    if not parsed["is_transaction"]:
        # OTPs, promos, payment reminders. 200 so the phone doesn't retry.
        return {"status": "ignored", "reason": "not a transaction SMS"}

    # MacroDroid retries on flaky mobile data; don't book the same spend twice.
    duplicate = (
        db.query(Transaction)
        .filter(
            Transaction.raw_text == text,
            Transaction.created_at >= utc_now_naive() - DEDUPE_WINDOW,
        )
        .first()
    )
    if duplicate:
        return {"status": "duplicate", "transaction": TransactionOut.model_validate(duplicate)}

    result = categorize(parsed["merchant"], text, parsed["direction"])
    txn = Transaction(
        raw_text=text,
        merchant=parsed["merchant"],
        amount=parsed["amount"],
        direction=parsed["direction"],
        category=result["category"],
        source="sms",
        needs_review=result["needs_review"],
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return {"status": "ok", "transaction": TransactionOut.model_validate(txn)}


@api.post("/transactions/manual", response_model=TransactionOut)
def add_manual(payload: ManualTransaction, db: Session = Depends(get_db)):
    txn = Transaction(
        merchant=payload.merchant,
        amount=payload.amount,
        direction=payload.direction,
        category=payload.category,
        source="manual",
        needs_review=False,
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


@api.get("/transactions", response_model=list[TransactionOut])
def list_transactions(
    month: int | None = Query(default=None, ge=1, le=12),
    year: int | None = Query(default=None, ge=2000, le=2100),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    q = db.query(Transaction)
    if month or year:
        m, y = _resolve_month(month, year)
        start, end = month_range_utc(y, m)
        q = q.filter(Transaction.created_at >= start, Transaction.created_at < end)
    return q.order_by(Transaction.created_at.desc(), Transaction.id.desc()).limit(limit).all()


@api.get("/transactions/needs-review", response_model=list[TransactionOut])
def needs_review(db: Session = Depends(get_db)):
    return (
        db.query(Transaction)
        .filter(Transaction.needs_review.is_(True))
        .order_by(Transaction.created_at.desc())
        .all()
    )


@api.patch("/transactions/{txn_id}/category", response_model=TransactionOut)
def update_category(txn_id: int, payload: CategoryUpdate, db: Session = Depends(get_db)):
    txn = db.get(Transaction, txn_id)
    if not txn:
        raise HTTPException(404, "Transaction not found")
    txn.category = payload.category
    txn.needs_review = False
    db.commit()
    db.refresh(txn)
    return txn


@api.delete("/transactions/{txn_id}")
def delete_transaction(txn_id: int, db: Session = Depends(get_db)):
    txn = db.get(Transaction, txn_id)
    if not txn:
        raise HTTPException(404, "Transaction not found")
    db.delete(txn)
    db.commit()
    return {"status": "deleted", "id": txn_id}


# --------------------------------------------------------------------- budgets

@api.get("/budget/summary", response_model=BudgetSummary)
def budget_summary(
    month: int | None = Query(default=None, ge=1, le=12),
    year: int | None = Query(default=None, ge=2000, le=2100),
    db: Session = Depends(get_db),
):
    m, y = _resolve_month(month, year)
    start, end = month_range_utc(y, m)

    rows = (
        db.query(Transaction.category, Transaction.direction, func.sum(Transaction.amount))
        .filter(Transaction.created_at >= start, Transaction.created_at < end)
        .group_by(Transaction.category, Transaction.direction)
        .all()
    )
    spent_map: dict[str, float] = {}
    total_income = 0.0
    for category, direction, total in rows:
        if direction == "debit":
            spent_map[category] = spent_map.get(category, 0.0) + float(total or 0)
        else:
            total_income += float(total or 0)

    limits = {b.category: b.monthly_limit for b in db.query(Budget).all()}

    # Show every category that has either a limit or some spend this month.
    result = []
    for category in sorted(set(limits) | set(spent_map)):
        spent = round(spent_map.get(category, 0.0), 2)
        limit = limits.get(category)
        result.append({
            "category": category,
            "limit": limit,
            "spent": spent,
            "remaining": round(limit - spent, 2) if limit is not None else None,
            "percent_used": round((spent / limit) * 100, 1) if limit else None,
        })
    # Over-budget first, then biggest spenders.
    result.sort(key=lambda c: (-(c["percent_used"] or 0), -c["spent"]))

    return {
        "month": m,
        "year": y,
        "total_spent": round(sum(spent_map.values()), 2),
        "total_income": round(total_income, 2),
        "total_budget": round(sum(limits.values()), 2),
        "categories": result,
    }


@api.get("/budget/limits")
def list_budgets(db: Session = Depends(get_db)):
    return [
        {"category": b.category, "monthly_limit": b.monthly_limit}
        for b in db.query(Budget).order_by(Budget.category).all()
    ]


@api.post("/budget/set")
def set_budget(payload: BudgetSet, db: Session = Depends(get_db)):
    existing = db.query(Budget).filter(Budget.category == payload.category).first()
    if payload.monthly_limit == 0:
        if existing:
            db.delete(existing)
            db.commit()
        return {"status": "cleared", "category": payload.category}
    if existing:
        existing.monthly_limit = payload.monthly_limit
    else:
        db.add(Budget(category=payload.category, monthly_limit=payload.monthly_limit))
    db.commit()
    return {"status": "ok", "category": payload.category, "monthly_limit": payload.monthly_limit}


# ----------------------------------------------------------------------- stats

@api.get("/stats/daily", response_model=TrendOut)
def daily_trend(
    month: int | None = Query(default=None, ge=1, le=12),
    year: int | None = Query(default=None, ge=2000, le=2100),
    db: Session = Depends(get_db),
):
    """Per-day debit totals for the month — the dashboard trend chart."""
    m, y = _resolve_month(month, year)
    start, end = month_range_utc(y, m)

    rows = (
        db.query(Transaction.created_at, Transaction.amount)
        .filter(
            Transaction.direction == "debit",
            Transaction.created_at >= start,
            Transaction.created_at < end,
        )
        .all()
    )
    # Bucket in Python so the local-timezone day boundary matches the month window.
    buckets: dict[str, float] = {}
    for created_at, amount in rows:
        key = local_day_key(created_at)
        buckets[key] = buckets.get(key, 0.0) + float(amount or 0)

    days = [
        {"date": f"{y:04d}-{m:02d}-{day:02d}",
         "spent": round(buckets.get(f"{y:04d}-{m:02d}-{day:02d}", 0.0), 2)}
        for day in range(1, days_in_month(y, m) + 1)
    ]
    return {"month": m, "year": y, "days": days}


@api.get("/categories")
def get_categories():
    return CATEGORIES


@api.get("/me")
def me():
    """Cheap endpoint the frontend hits to validate a token at login."""
    return {"status": "ok"}


app.include_router(api)


@app.get("/health")
def health():
    """Unauthenticated so Railway's healthcheck can reach it."""
    return {"status": "ok"}

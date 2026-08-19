import asyncio
import os
import secrets
import time
from datetime import timedelta

from dotenv import load_dotenv

# Must run before any module that reads env vars at import time (auth, categorizer).
# utf-8-sig so a .env saved by a Windows editor (BOM) still parses.
load_dotenv(encoding="utf-8-sig")

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import HTMLResponse, RedirectResponse  # noqa: E402
from sqlalchemy import func  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import gmail_poll  # noqa: E402
from auth import AUTH_TOKEN, require_token  # noqa: E402
from categorizer import CATEGORIES, categorize, parse_sms, payee_key_for  # noqa: E402
from db import get_db, init_db  # noqa: E402
from models import Budget, FuelFill, GmailAuth, LendingReminder, Payee, Todo, Transaction, Vehicle  # noqa: E402
from receipt_scan import ReceiptScanError, gemini_configured, scan_receipt  # noqa: E402
from schemas import (  # noqa: E402
    BudgetSet,
    BudgetSummary,
    CategoryUpdate,
    FuelFillIn,
    FuelFillOut,
    ImportRequest,
    LendingBalance,
    ManualTransaction,
    MerchantUpdate,
    MileageOut,
    ReceiptScanOut,
    SMSPayload,
    TodoIn,
    TodoOut,
    TodoUpdate,
    TransactionClassify,
    TransactionOut,
    TrendOut,
    VehicleIn,
    VehicleOut,
)
from timeutil import (  # noqa: E402
    days_in_month,
    local_day_key,
    local_now,
    month_anchor_utc,
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


def _resolve_kind(kind_choice: str, direction: str, label: str, requested_category: str | None, fallback_category: str) -> tuple[str, str, str | None]:
    """Turns a 'what is this?' answer (expense/friend/wallet/self) plus the
    money's actual direction into (real kind, category, counterparty).
    Shared by classify_transaction() (the Review tab's answer to an
    SMS-ingested transaction) and add_manual() (the same answer, asked
    up front for a manually-logged one) — one place decides lend vs.
    repayment, top-up vs. transfer, so the two entry points can't drift
    apart on this.
    """
    is_debit = direction == "debit"
    if kind_choice == "friend":
        return ("lend" if is_debit else "repayment"), (requested_category or "Lending"), label
    if kind_choice == "friend_settle":
        # The mirror of "friend": a debit here is paying back a debt *you*
        # owe (not lending anything new out), and a credit is someone
        # lending *you* money (not repaying an existing loan of yours).
        # Neither should move the Lending tab's "who owes you" balance —
        # lending_balances() only ever looks at kind in (lend, repayment),
        # so filing this as a plain transfer keeps it out of that ledger
        # entirely while still recording who it was with.
        return "transfer", (requested_category or "Transfer"), label
    if kind_choice == "wallet":
        return ("topup" if is_debit else "transfer"), (requested_category or "Transfer"), None
    if kind_choice == "self":
        return "transfer", (requested_category or "Transfer"), None
    return ("expense" if is_debit else "income"), (requested_category or fallback_category), None


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
    # _ingest() can call out to Claude synchronously (categorizer.py) when
    # ANTHROPIC_API_KEY is set. This route is `async def` (it awaits
    # request.body() above), so unlike a plain `def` route — which FastAPI
    # runs in a thread pool automatically — a blocking call here runs
    # directly on the event loop and freezes every other request, including
    # /health, for as long as the call takes. Same bug class that took the
    # server down via /ai/scan-receipt; fixed there and here together.
    return await asyncio.to_thread(_ingest, text[:2000], db)


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

    merchant = parsed["merchant"]
    direction = parsed["direction"]
    payee_key = payee_key_for(merchant)
    known: Payee | None = db.get(Payee, payee_key) if payee_key else None

    kind = "income" if direction == "credit" else "expense"
    category = "Income" if direction == "credit" else "Uncategorized"
    needs_review = False
    counterparty = None
    note = merchant

    if known:
        # Already told what this counterparty is — trust it, never re-ask.
        note = known.label
        if known.kind == "friend":
            kind = "lend" if direction == "debit" else "repayment"
            counterparty = known.label
            category = "Lending"
        elif known.kind == "wallet":
            kind = "topup" if direction == "debit" else "transfer"
            category = "Transfer"
        elif known.kind == "self":
            kind = "transfer"
            category = "Transfer"
        elif known.kind == "expense" and direction == "debit":
            # Payee.kind uses the same vocabulary as TransactionClassify.kind
            # ("expense", not "merchant") — see classify_transaction().
            category = known.default_category or "Uncategorized"
    else:
        result = categorize(merchant, text, direction)
        category = result["category"]
        needs_review = result["needs_review"]
        # A brand-new counterparty (no rule match, so `categorize` couldn't
        # have known it's actually a friend or a wallet) — ask once via the
        # review queue instead of silently filing it as a plain expense,
        # even when the AI was confident about the category. Never applies
        # to credits: a stranger paying you is income either way.
        if payee_key and direction == "debit" and result["source"] != "rule":
            needs_review = True

    txn = Transaction(
        raw_text=text,
        merchant=note,
        amount=parsed["amount"],
        direction=direction,
        category=category,
        source="sms",
        needs_review=needs_review,
        kind=kind,
        payee_key=payee_key,
        counterparty=counterparty,
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return {"status": "ok", "transaction": TransactionOut.model_validate(txn)}


@app.get("/gmail/auth/start")
def gmail_auth_start(token: str = ""):
    """One-time, done by hand in a browser — see gmail_poll.py's module
    docstring. Not behind the `api` router (a browser redirect can't carry
    an Authorization header) — gated instead by the same shared secret as a
    query param, so only whoever holds AUTH_TOKEN can (re)point the poller
    at a Gmail account."""
    if not secrets.compare_digest(token, AUTH_TOKEN):
        raise HTTPException(401, "Invalid or missing token")
    if not gmail_poll.configured():
        raise HTTPException(503, "GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI not set on the server")
    return RedirectResponse(gmail_poll.auth_url())


@app.get("/gmail/auth/callback")
def gmail_auth_callback(code: str = "", error: str = "", db: Session = Depends(get_db)):
    """Google's redirect target after the user approves (or declines) access."""
    if error:
        return HTMLResponse(f"<p>Google returned an error: {error}</p>", status_code=400)
    if not code:
        raise HTTPException(422, "Missing code")
    try:
        tokens = gmail_poll.exchange_code(code)
    except gmail_poll.GmailPollError as e:
        return HTMLResponse(f"<p>Token exchange failed: {e}</p>", status_code=502)
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        # Google only issues a refresh_token on first consent, or when
        # prompt=consent forces re-issue — auth_url() always sets that, so
        # this shouldn't normally happen.
        return HTMLResponse(
            "<p>No refresh token in Google's response. Revoke this app's access at "
            "myaccount.google.com/permissions and try the link again.</p>",
            status_code=502,
        )
    existing = db.get(GmailAuth, 1)
    if existing:
        existing.refresh_token = refresh_token
    else:
        db.add(GmailAuth(id=1, refresh_token=refresh_token))
    db.commit()
    return HTMLResponse("<p>Gmail connected — you can close this tab.</p>")


@api.post("/gmail/poll")
async def gmail_poll_endpoint(db: Session = Depends(get_db)):
    """Hit on a schedule by .github/workflows/gmail-poll.yml, same pattern as
    keep-alive.yml. Async + asyncio.to_thread() for the same reason as
    /sms/ingest/raw and /ai/scan-receipt: several sequential HTTPS calls to
    Google easily add up to a few seconds, and running that directly on the
    event loop would freeze every other request (including /health) for as
    long as it takes."""
    row = db.get(GmailAuth, 1)
    if not row:
        raise HTTPException(400, "Gmail not connected yet — visit /gmail/auth/start?token=<AUTH_TOKEN> once")

    try:
        texts = await asyncio.to_thread(gmail_poll.fetch_new_alerts, row.refresh_token, row.last_poll_at)
    except gmail_poll.GmailPollError as e:
        raise HTTPException(502, str(e)) from e

    results = [_ingest(text, db) for text in texts]
    row.last_poll_at = utc_now_naive()
    db.commit()
    return {"checked": len(texts), "results": results}


@api.post("/transactions/manual", response_model=TransactionOut)
def add_manual(payload: ManualTransaction, db: Session = Depends(get_db)):
    label = payload.merchant or ("Someone" if payload.kind in ("friend", "friend_settle") else "Transaction")
    fallback_category = payload.category or "Uncategorized"
    kind, category, counterparty = _resolve_kind(payload.kind, payload.direction, label, payload.category, fallback_category)

    txn = Transaction(
        merchant=label if payload.kind != "expense" else payload.merchant,
        amount=payload.amount,
        direction=payload.direction,
        category=category,
        source="manual",
        needs_review=False,
        kind=kind,
        counterparty=counterparty,
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8MB — generous for a phone photo, small enough to stay fast


@api.get("/ai/status")
def ai_status():
    """Lets the frontend hide the photo-scan UI entirely when no key is set,
    instead of showing a button that always fails."""
    return {"receipt_scan_available": gemini_configured()}


@api.post("/ai/scan-receipt", response_model=ReceiptScanOut)
async def scan_receipt_endpoint(
    image: UploadFile = File(...),
    note: str | None = Form(default=None),
):
    """Reads a photo of a receipt/payment screenshot and returns what it
    found — amount, merchant, category, direction — without booking
    anything yet. `note` is free text alongside the image — e.g. "this was
    for a friend's birthday, categorise as Entertainment" — treated as
    authoritative context, the same way a correction to a human assistant
    would be.

    Deliberately a preview, not a create: the model has no way to know a
    scanned payment screenshot was actually money sent to a friend rather
    than a shop purchase, so the frontend runs the result through the same
    shop/person/wallet/self chooser as a manual entry (see AddExpense.jsx)
    before anything is saved via POST /transactions/manual — otherwise every
    photo-scanned lend or wallet top-up would get silently booked as a plain
    expense and skew totals exactly the way CLAUDE.md warns against.
    """
    if not gemini_configured():
        raise HTTPException(503, "Photo scanning isn't set up (no GEMINI_API_KEY on the server)")

    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(422, "Empty image")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image too large (max 8MB)")

    try:
        # scan_receipt() blocks on urllib for up to 30s (longer with retries
        # during a Gemini demand spike) — this endpoint is async, so running
        # it directly freezes the single-worker event loop, and with it every
        # other request including /health, for the same duration. That's what
        # actually took the server down in testing: Render's health check
        # timed out at 5s while a scan was in flight and restarted the
        # instance. asyncio.to_thread() runs the blocking call off the event
        # loop so /health keeps responding no matter how long Gemini takes.
        result = await asyncio.to_thread(scan_receipt, image_bytes, image.content_type or "image/jpeg", note)
    except ReceiptScanError as e:
        raise HTTPException(502, str(e)) from e
    except Exception as e:  # noqa: BLE001 — a raw TimeoutError already slipped
        # past receipt_scan.py's specific handlers once in production (fixed
        # there too, see receipt_scan.py) and came out as an unhandled 500.
        # This is the backstop: whatever unexpected error type shows up next,
        # it fails as a clean message instead of a bare server error.
        raise HTTPException(502, f"Couldn't read that image: {e}") from e

    if result["amount"] <= 0:
        raise HTTPException(422, "Couldn't read an amount from that image — try a clearer photo")

    return result


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


def _recategorize_pending_sync(db: Session) -> dict:
    pending = (
        db.query(Transaction)
        .filter(Transaction.needs_review.is_(True), Transaction.direction == "debit")
        .all()
    )
    updated = 0
    for i, txn in enumerate(pending):
        if i > 0:
            # Gemini's free tier caps requests per minute — firing a whole
            # backlog back-to-back blew straight through it (confirmed via
            # /debug/gemini-test: HTTP 429), so every call in the first real
            # run silently failed and nothing got updated. ~4s/call keeps
            # this comfortably under a 15 RPM ceiling.
            time.sleep(4)
        result = categorize(txn.merchant or "", txn.raw_text or "", txn.direction)
        if result["source"] == "ai_confident" and result["category"] != txn.category:
            txn.category = result["category"]
            updated += 1
    db.commit()
    return {"checked": len(pending), "updated": updated}


@api.get("/debug/gemini-test")
def debug_gemini_test():
    """Temporary, round 2 — pacing didn't fix recategorize-pending (still
    0/28 updated), so this isn't just the per-minute rate limit. Remove once
    root-caused."""
    import traceback

    from categorizer import _gemini_categorize
    try:
        result = _gemini_categorize("FRESH SUPERMARKET PERAMBUR C1", "Rs.194.00 debited towards FRESH SUPERMARKET PERAMBUR C1", _debug=True)
        return {"result": result}
    except Exception as e:
        return {"error": repr(e), "trace": traceback.format_exc()}


@api.post("/transactions/recategorize-pending")
async def recategorize_pending(db: Session = Depends(get_db)):
    """Maintenance action: re-runs categorize() against everything still
    sitting in Review and updates just the category field when a confident
    AI result comes back — needs_review is left untouched, so the shop /
    person / wallet / my account question still gets asked, just with a
    correct category pre-filled in Review's chip picker instead of always
    defaulting to Food & Dining. Exists because a large batch of Gmail-
    backfilled transactions predates Gemini being wired into categorize(),
    so they never got a real categorization pass. Runs each merchant through
    a Gemini call sequentially — easily tens of seconds for a big backlog —
    hence async + to_thread, same reasoning as every other slow route here."""
    return await asyncio.to_thread(_recategorize_pending_sync, db)


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


@api.patch("/transactions/{txn_id}/merchant", response_model=TransactionOut)
def update_merchant(txn_id: int, payload: MerchantUpdate, db: Session = Depends(get_db)):
    """Renames just the merchant/note — used to fill in a location-resolved
    place name after adding an expense with no merchant typed, without
    touching category/kind/review status."""
    txn = db.get(Transaction, txn_id)
    if not txn:
        raise HTTPException(404, "Transaction not found")
    txn.merchant = payload.merchant
    db.commit()
    db.refresh(txn)
    return txn


@api.patch("/transactions/{txn_id}/classify", response_model=TransactionOut)
def classify_transaction(txn_id: int, payload: TransactionClassify, db: Session = Depends(get_db)):
    """The Review tab's 'who is this?' answer: merchant / friend / wallet / my
    own account. Resolves the transaction's real kind (lend vs. repayment,
    top-up vs. transfer — depends on the original debit/credit direction) and,
    by default, remembers the payee so this is never asked again."""
    txn = db.get(Transaction, txn_id)
    if not txn:
        raise HTTPException(404, "Transaction not found")

    label = payload.label or txn.merchant or "Transaction"
    txn.kind, txn.category, txn.counterparty = _resolve_kind(
        payload.kind, txn.direction, label, payload.category, txn.category
    )
    txn.merchant = label
    txn.needs_review = False

    if payload.remember and txn.payee_key:
        payee = db.get(Payee, txn.payee_key)
        remembered_category = txn.category if payload.kind == "expense" else None
        if payee is None:
            payee = Payee(key=txn.payee_key, label=label, kind=payload.kind, default_category=remembered_category)
            db.add(payee)
        else:
            payee.label = label
            payee.kind = payload.kind
            payee.default_category = remembered_category

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


@api.post("/transactions/import")
def import_transactions(payload: ImportRequest, db: Session = Depends(get_db)):
    """Bulk-loads historical expenses that only have a month, not an exact
    date (e.g. transcribed from an old spreadsheet). Booked to noon on the
    1st of each item's month, kind='expense', source='import' — counts
    toward that month's budget total but is excluded from the daily trend
    chart (see daily_trend()) so it doesn't draw a fake spike on the 1st.

    Guards against an accidental double-run: refuses if an import has
    already happened, unless `force: true` is explicitly passed.
    """
    if not payload.force:
        existing = db.query(Transaction).filter(Transaction.source == "import").first()
        if existing:
            count = db.query(Transaction).filter(Transaction.source == "import").count()
            return {
                "status": "already_imported",
                "existing_count": count,
                "detail": "An import already ran. Pass force=true to add these on top anyway.",
            }

    added = []
    for item in payload.items:
        txn = Transaction(
            amount=item.amount,
            direction="debit",
            category=item.category,
            merchant=item.merchant,
            source="import",
            needs_review=False,
            kind="expense",
            created_at=month_anchor_utc(item.year, item.month),
        )
        db.add(txn)
        added.append(txn)

    db.commit()
    return {
        "status": "ok",
        "added": len(added),
        "total": round(sum(item.amount for item in payload.items), 2),
    }


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
        db.query(Transaction.category, Transaction.kind, func.sum(Transaction.amount))
        .filter(Transaction.created_at >= start, Transaction.created_at < end)
        .group_by(Transaction.category, Transaction.kind)
        .all()
    )
    # Only "expense" is real spending. topup/transfer/lend/repayment
    # deliberately never reach spent_map — a wallet load or money lent to a
    # friend is not a purchase, and counting it as one is exactly the kind of
    # naive-tracker bug this app exists to avoid.
    spent_map: dict[str, float] = {}
    total_income = 0.0
    for category, kind, total in rows:
        if kind == "expense":
            spent_map[category] = spent_map.get(category, 0.0) + float(total or 0)
        elif kind == "income":
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
    """Per-day expense totals for the month — the dashboard trend chart.

    Imported historical rows (source="import") only ever know a month, not a
    real day — they're booked to the 1st at noon (see /transactions/import),
    which would otherwise draw a fake spike there. They still count toward
    the month's total via /budget/summary; they're just excluded from this
    day-by-day view specifically.
    """
    m, y = _resolve_month(month, year)
    start, end = month_range_utc(y, m)

    rows = (
        db.query(Transaction.created_at, Transaction.amount)
        .filter(
            Transaction.kind == "expense",
            Transaction.source != "import",
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


# ---------------------------------------------------------------------- fuel

DEFAULT_VEHICLES = [
    {"id": "activa", "name": "Activa", "type": "scooter", "fuel": "petrol", "tank_capacity_l": 5.3},
    {"id": "speed400", "name": "Speed 400", "type": "motorcycle", "fuel": "petrol", "tank_capacity_l": 13.0},
    {"id": "swiftdzire", "name": "Swift Dzire", "type": "car", "fuel": "petrol", "tank_capacity_l": 42.0},
]


@api.get("/vehicles", response_model=list[VehicleOut])
def list_vehicles(db: Session = Depends(get_db)):
    if db.query(Vehicle).count() == 0:
        # First run: seed the three known vehicles. `merge` (upsert), not
        # add — makes a concurrent duplicate call harmless instead of a
        # duplicate-key error.
        for v in DEFAULT_VEHICLES:
            db.merge(Vehicle(**v, archived=False))
        db.commit()
    return db.query(Vehicle).order_by(Vehicle.archived, Vehicle.name).all()


@api.post("/vehicles", response_model=VehicleOut)
def upsert_vehicle(payload: VehicleIn, db: Session = Depends(get_db)):
    existing = db.get(Vehicle, payload.id)
    if existing:
        existing.name = payload.name
        existing.type = payload.type
        existing.fuel = payload.fuel
        existing.tank_capacity_l = payload.tank_capacity_l
    else:
        db.add(Vehicle(**payload.model_dump(), archived=False))
    db.commit()
    return db.get(Vehicle, payload.id)


@api.patch("/vehicles/{vehicle_id}/archive")
def archive_vehicle(vehicle_id: str, db: Session = Depends(get_db)):
    vehicle = db.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(404, "Vehicle not found")
    vehicle.archived = True
    db.commit()
    return {"status": "archived", "id": vehicle_id}


@api.get("/fuel/fills", response_model=list[FuelFillOut])
def list_fuel_fills(vehicle_id: str | None = Query(default=None), db: Session = Depends(get_db)):
    q = db.query(FuelFill)
    if vehicle_id:
        q = q.filter(FuelFill.vehicle_id == vehicle_id)
    return q.order_by(FuelFill.created_at).all()


@api.post("/fuel/fills", response_model=FuelFillOut)
def add_fuel_fill(payload: FuelFillIn, db: Session = Depends(get_db)):
    if not db.get(Vehicle, payload.vehicle_id):
        raise HTTPException(404, "Vehicle not found")
    fill = FuelFill(**payload.model_dump())
    db.add(fill)
    db.commit()
    db.refresh(fill)
    return fill


@api.delete("/fuel/fills/{fill_id}")
def delete_fuel_fill(fill_id: int, db: Session = Depends(get_db)):
    fill = db.get(FuelFill, fill_id)
    if not fill:
        raise HTTPException(404, "Fill not found")
    db.delete(fill)
    db.commit()
    return {"status": "deleted", "id": fill_id}


@api.get("/fuel/mileage", response_model=MileageOut)
def fuel_mileage(vehicle_id: str, db: Session = Depends(get_db)):
    """km/L is only derived between two CONSECUTIVE full-tank fills with
    odometer readings — a partial fill, or an odometer that went backwards
    (reset or bad entry), is skipped as a leg endpoint rather than producing a
    fabricated number."""
    fills = (
        db.query(FuelFill)
        .filter(FuelFill.vehicle_id == vehicle_id)
        .order_by(FuelFill.created_at)
        .all()
    )

    total_spent = sum(f.amount for f in fills)
    total_liters = sum(f.liters or 0 for f in fills)
    priced = [f for f in fills if f.liters]
    avg_price = sum(f.amount / f.liters for f in priced) / len(priced) if priced else None

    full = [f for f in fills if f.is_full_tank and f.odometer is not None and f.liters]
    legs = []
    for prev, cur in zip(full, full[1:]):
        km = cur.odometer - prev.odometer
        if km <= 0 or not cur.liters:
            continue  # odometer reset or bad entry — skip, don't fabricate
        legs.append({
            "from_fill_id": prev.id,
            "to_fill_id": cur.id,
            "km": km,
            "liters": cur.liters,
            "km_per_liter": km / cur.liters,
            "cost_per_km": cur.amount / km,
        })

    avg_mileage = sum(leg["km_per_liter"] for leg in legs) / len(legs) if legs else None

    return {
        "vehicle_id": vehicle_id,
        "total_spent": round(total_spent, 2),
        "total_liters": round(total_liters, 2),
        "avg_price_per_liter": round(avg_price, 2) if avg_price else None,
        "avg_mileage": round(avg_mileage, 2) if avg_mileage else None,
        "last_mileage": round(legs[-1]["km_per_liter"], 2) if legs else None,
        "legs": legs,
    }


# ---------------------------------------------------------------------- todos

@api.get("/todos", response_model=list[TodoOut])
def list_todos(db: Session = Depends(get_db)):
    return db.query(Todo).order_by(Todo.order).all()


@api.post("/todos", response_model=TodoOut)
def add_todo(payload: TodoIn, db: Session = Depends(get_db)):
    first = db.query(Todo).order_by(Todo.order).first()
    # New items go to the top, so adding one doesn't bury it under a long list.
    order = (first.order - 1) if first else 0
    todo = Todo(text=payload.text, order=order)
    db.add(todo)
    db.commit()
    db.refresh(todo)
    return todo


@api.patch("/todos/{todo_id}", response_model=TodoOut)
def update_todo(todo_id: int, payload: TodoUpdate, db: Session = Depends(get_db)):
    todo = db.get(Todo, todo_id)
    if not todo:
        raise HTTPException(404, "Todo not found")
    if payload.text is not None:
        todo.text = payload.text
    if payload.done is not None:
        todo.done = payload.done
        todo.completed_at = utc_now_naive() if payload.done else None
    db.commit()
    db.refresh(todo)
    return todo


@api.delete("/todos/{todo_id}")
def delete_todo(todo_id: int, db: Session = Depends(get_db)):
    todo = db.get(Todo, todo_id)
    if not todo:
        raise HTTPException(404, "Todo not found")
    db.delete(todo)
    db.commit()
    return {"status": "deleted", "id": todo_id}


@api.post("/todos/clear-completed")
def clear_completed_todos(db: Session = Depends(get_db)):
    done = db.query(Todo).filter(Todo.done.is_(True)).all()
    for t in done:
        db.delete(t)
    db.commit()
    return {"status": "ok", "cleared": len(done)}


# -------------------------------------------------------------------- lending

@api.get("/lending", response_model=list[LendingBalance])
def lending_balances(db: Session = Depends(get_db)):
    rows = (
        db.query(Transaction)
        .filter(Transaction.kind.in_(("lend", "repayment")))
        .all()
    )
    reminders = {r.person: r for r in db.query(LendingReminder).all()}

    balances: dict[str, dict] = {}
    for t in rows:
        person = t.counterparty or t.merchant or "Someone"
        b = balances.setdefault(person, {"person": person, "lent": 0.0, "repaid": 0.0})
        if t.kind == "lend":
            b["lent"] += t.amount
        else:
            b["repaid"] += t.amount

    out = []
    for person, b in balances.items():
        outstanding = round(b["lent"] - b["repaid"], 2)
        reminder = reminders.get(person)
        out.append({
            "person": person,
            "lent": round(b["lent"], 2),
            "repaid": round(b["repaid"], 2),
            "outstanding": outstanding,
            "next_reminder_at": (reminder.next_reminder_at.isoformat() + "Z") if reminder else None,
        })
    return sorted(out, key=lambda x: -x["outstanding"])


@api.post("/lending/{person}/snooze")
def snooze_lending_reminder(person: str, days: int = Query(default=3, ge=1, le=90), db: Session = Depends(get_db)):
    next_at = utc_now_naive() + timedelta(days=days)
    reminder = db.get(LendingReminder, person)
    if reminder:
        reminder.next_reminder_at = next_at
        reminder.snooze_days = days
    else:
        db.add(LendingReminder(person=person, next_reminder_at=next_at, snooze_days=days))
    db.commit()
    return {"status": "ok", "person": person, "next_reminder_at": next_at.isoformat() + "Z"}


@api.delete("/lending/{person}/reminder")
def clear_lending_reminder(person: str, db: Session = Depends(get_db)):
    reminder = db.get(LendingReminder, person)
    if reminder:
        db.delete(reminder)
        db.commit()
    return {"status": "ok", "person": person}


app.include_router(api)


@app.get("/health")
def health():
    """Unauthenticated so Railway's healthcheck can reach it."""
    return {"status": "ok"}

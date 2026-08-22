import asyncio
import os
import re
import secrets
import time
import uuid
import urllib.request
from contextlib import asynccontextmanager
from datetime import date as date_cls
from datetime import datetime as datetime_cls
from datetime import timedelta

from dotenv import load_dotenv

# Must run before any module that reads env vars at import time (auth, categorizer).
# utf-8-sig so a .env saved by a Windows editor (BOM) still parses.
load_dotenv(encoding="utf-8-sig")

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import HTMLResponse, RedirectResponse  # noqa: E402
from sqlalchemy import func  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import gmail_poll  # noqa: E402
from auth import AUTH_TOKEN, require_token  # noqa: E402
from categorizer import (  # noqa: E402
    CATEGORIES,
    business_hint,
    categorize,
    parse_alert_date,
    parse_bank_ref,
    parse_sms,
    payee_key_for,
)
from db import get_db, init_db  # noqa: E402
from models import Budget, FuelFill, GmailAuth, LendingReminder, Payee, Todo, Transaction, Vehicle  # noqa: E402
from receipt_scan import ReceiptScanError, gemini_configured, scan_receipt, scan_statement  # noqa: E402
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
    PayeeOut,
    PayeeUpdate,
    ReceiptScanOut,
    SMSPayload,
    ScreenshotImport,
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
    period_label,
    period_range_utc,
    local_date_to_utc,
    local_day_key,
    local_now,
    month_anchor_utc,
    month_range_utc,
    utc_now_naive,
)

# Render's free tier spins the instance down after ~15 minutes with no
# inbound traffic, and the next request then pays a ~60s cold start — which
# is exactly what the app's endless "Loading…" was: not slow code, a
# sleeping server. .github/workflows/keep-alive.yml was meant to prevent
# this by pinging every 10 minutes, but GitHub silently drops scheduled runs
# on free repos: real gaps observed between consecutive runs were 34 and 51
# minutes, far past the spin-down window.
#
# So the service keeps itself awake instead. A request to its own *public*
# URL routes back in through Render's proxy and counts as inbound traffic,
# which is what resets the idle timer. RENDER_EXTERNAL_URL is injected by
# Render automatically, so this needs no configuration there and stays off
# locally (where the variable doesn't exist).
#
# Cost note: staying up 24/7 uses ~730 of the free tier's 750 instance
# hours/month. That fits, but it means this service should be the only free
# web service in the account, or the hours run out before month end.
KEEP_ALIVE_URL = os.getenv("KEEP_ALIVE_URL", os.getenv("RENDER_EXTERNAL_URL", "")).strip().rstrip("/")
KEEP_ALIVE_INTERVAL_SECONDS = 540  # 9 min, comfortably inside the 15 min window


async def _keep_awake() -> None:
    while True:
        await asyncio.sleep(KEEP_ALIVE_INTERVAL_SECONDS)
        try:
            # to_thread: urlopen is blocking, and this runs on the same event
            # loop that serves every request — same reasoning as /gmail/poll.
            await asyncio.to_thread(
                urllib.request.urlopen, f"{KEEP_ALIVE_URL}/health", None, 30
            )
        except Exception:
            # A failed self-ping must never take the server down with it;
            # the next tick tries again in 9 minutes.
            pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(_keep_awake()) if KEEP_ALIVE_URL else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Budget Tracker API", lifespan=lifespan)

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

# Channels that deliver a transaction without anyone doing anything. Everything
# else ("manual", "screenshot", "import") is the user typing, and says nothing
# about whether automatic capture is still alive.
AUTOMATED_SOURCES = ("sms", "gmail")

# How long automatic capture may stay silent before the app says so.
#
# The whole thing rests on alerts arriving, and when they stop it fails
# *silently*: MacroDroid stopped forwarding SMS for two days (Android revoked
# its permission under "pause app activity if unused") and the app went on
# looking perfectly healthy, just with less in it. Nothing was wrong on
# screen — which is precisely the problem, and why this is worth a warning
# rather than a log line.
#
# 36 hours, because every day in the live history except one carried at least
# one alert, so a day and a half of total silence is genuinely unusual. A
# false alarm costs a glance at a dismissible line; a missed outage costs
# every transaction until someone happens to notice.
CAPTURE_QUIET_HOURS = float(os.getenv("CAPTURE_QUIET_HOURS", "36"))


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


def _ingest(text: str, db: Session, source: str = "sms"):
    """Store one bank alert. `source` records the channel it arrived by —
    "sms" (MacroDroid) or "gmail" (the poller) — so capture_health() can say
    *which* pipe went quiet rather than only that something did. Both feed
    identical text through identical parsing; only the label differs."""
    parsed = parse_sms(text)

    if not parsed["is_transaction"]:
        # OTPs, promos, payment reminders. 200 so the phone doesn't retry.
        return {"status": "ignored", "reason": "not a transaction SMS"}

    # The bank's own reference is checked FIRST and with no time window at
    # all: the same reference is the same payment, whether the second copy
    # arrives seconds later as a retry or hours later as the email version of
    # an SMS already ingested. Text matching alone cannot see that — an SMS
    # and an email describe one payment in completely different words, which
    # is exactly how two real payments got booked twice here.
    bank_ref = parse_bank_ref(text)
    if bank_ref:
        already = db.query(Transaction).filter(Transaction.bank_ref == bank_ref).first()
        if already:
            return {"status": "duplicate", "transaction": TransactionOut.model_validate(already)}

    # Fallback for alerts carrying no reference: identical text in a short
    # window, which still covers MacroDroid retrying on flaky mobile data.
    duplicate = (
        db.query(Transaction)
        .filter(
            Transaction.raw_text == text,
            # ingested_at, NOT created_at: created_at is the bank's date now,
            # so it can sit hours in the past and this window would match
            # nothing at all — see the ingested_at comment in models.py.
            Transaction.ingested_at >= utc_now_naive() - DEDUPE_WINDOW,
        )
        .first()
    )
    if duplicate:
        return {"status": "duplicate", "transaction": TransactionOut.model_validate(duplicate)}

    merchant = parsed["merchant"]
    direction = parsed["direction"]
    # Identity keys off the VPA when the message carries one, NOT off the
    # display name — the name is now the human-readable merchant (e.g.
    # "Ss Hyderabad Biriyani Peravallur") and a shop can present slightly
    # different name text across messages, while its VPA is stable. Falls
    # back to a normalised name key for card swipes, which have no VPA.
    #
    # A credit whose alert never named a sender is the exception: `merchant`
    # there is a placeholder describing the receiving account ("Credit to
    # HDFC ...9393"), not a counterparty. Keying off it would remember that
    # placeholder as a payee and quietly fold every future anonymous credit,
    # from anyone, into one identity that is never asked about again.
    named = parsed.get("has_counterparty", True)
    payee_key = parsed.get("upi_id") or (payee_key_for(merchant) if named else None)
    known: Payee | None = db.get(Payee, payee_key) if payee_key else None

    kind = "income" if direction == "credit" else "expense"
    category = "Income" if direction == "credit" else "Uncategorized"
    needs_review = False
    counterparty = None
    note = merchant

    if known:
        # Already told what this counterparty is — trust it, never re-ask.
        #
        # Delegates to _resolve_kind rather than repeating the mapping, which
        # is the entire reason that helper exists. This branch used to hand-
        # roll its own chain and drifted: "friend_settle" (the "paying back
        # what I owe" answer) was added to _resolve_kind but never here, so it
        # matched no case, fell through to a plain expense, and silently
        # counted a debt settlement as spending. Payee.kind and
        # TransactionClassify.kind share one vocabulary — keep one reader of it.
        note = known.label
        # default_category is only ever stored for "expense" payees, and only
        # meaningful on a debit: a refund from a shop is income, not another
        # helping of that shop's category.
        remembered_category = known.default_category if direction == "debit" else None
        kind, category, counterparty = _resolve_kind(
            known.kind, direction, known.label, remembered_category, category
        )
    else:
        result = categorize(merchant, text, direction)
        category = result["category"]
        needs_review = result["needs_review"]
        # A brand-new counterparty could be a friend or a wallet rather than a
        # shop, and getting that wrong counts lending as spending — so the
        # default is to ask once via the review queue rather than file it
        # silently, even when the category itself looked confident.
        #
        # The one exception is a name that answers the question by itself:
        # "INIYA MUGIL SOUP" or "GEETHAM DINE IN 1" is obviously a business,
        # and making the user tap "A shop" on those is pure noise. Requires
        # BOTH a confident categorisation and an explicit entity="business";
        # "person" and "unclear" still ask, so the safe direction stays the
        # default and only the obvious cases are skipped.
        #
        obvious_business = result["source"] == "ai_confident" and result.get("entity") == "business"
        if payee_key and direction == "debit" and result["source"] != "rule" and not obvious_business:
            needs_review = True

        # Credits used to be filed as income unconditionally, on the reasoning
        # that "a stranger paying you is income either way". That is wrong for
        # the most common credit of all: moving money in from your OWN other
        # bank. A real Rs 10,000 self-transfer sat counted as income here,
        # visible only because the alert names the sender —
        #   "Sender: VISHNU ARAVIND R G (VPA: rgvishnuaravind@oksbi)"
        # and that VPA belongs to the account holder.
        #
        # Scoped to credits carrying a real UPI id, which is what a
        # person-to-person or own-account transfer looks like. A salary credit
        # ("by salary transfer from ACME CORP") has only a name-derived key,
        # is unambiguously income, and must not be dragged into the queue —
        # a first cut of this asked about those too.
        #
        # The answer is durable either way: say "my account" once and every
        # future transfer from it is classified without asking again.
        if payee_key and "@" in payee_key and direction == "credit":
            needs_review = True

        # Money arrived and the alert did not say from whom. That is worth a
        # question rather than a silent "income" row: a real Rs 10,000 credit
        # sat filed as income from "Unknown" for three weeks, and no screen in
        # the app had any reason to mention it. There is no payee_key to
        # remember here (see above), so this asks once per such credit — which
        # is correct, since each one is a different unknown sender.
        if direction == "credit" and not named:
            needs_review = True

    # Date the transaction when the BANK says it happened, not when we
    # happened to read the alert. Guarded three ways: a date we can't parse
    # falls back to now, a future date is rejected outright (a mis-parse, or
    # a bank writing MM-DD), and anything older than a year is ignored rather
    # than silently rewriting history far outside the app's own data.
    occurred_at = None
    alert_date = parse_alert_date(text)
    if alert_date:
        candidate = local_date_to_utc(*alert_date)
        now = utc_now_naive()
        if candidate <= now + timedelta(days=1) and candidate >= now - timedelta(days=365):
            occurred_at = candidate

    txn = Transaction(
        raw_text=text,
        merchant=note,
        amount=parsed["amount"],
        direction=direction,
        category=category,
        source=source,
        needs_review=needs_review,
        kind=kind,
        payee_key=payee_key,
        counterparty=counterparty,
        bank_ref=bank_ref,
        **({"created_at": occurred_at} if occurred_at else {}),
    )
    db.add(txn)
    try:
        db.commit()
    except IntegrityError:
        # The unique index on bank_ref fired: another request inserted this
        # same payment between our existence check above and this commit.
        # That check cannot be made safe on its own — six concurrent copies of
        # one alert once all passed it and booked Rs 333 six times. The
        # database is the arbiter; losing the race simply means the other
        # request already stored it.
        db.rollback()
        winner = db.query(Transaction).filter(Transaction.bank_ref == bank_ref).first()
        if winner:
            return {"status": "duplicate", "transaction": TransactionOut.model_validate(winner)}
        raise
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

    results = [_ingest(text, db, source="gmail") for text in texts]
    row.last_poll_at = utc_now_naive()
    db.commit()
    return {"checked": len(texts), "results": results}


@api.post("/transactions/manual", response_model=TransactionOut)
def add_manual(payload: ManualTransaction, db: Session = Depends(get_db)):
    label = payload.merchant or ("Someone" if payload.kind in ("friend", "friend_settle") else "Transaction")
    fallback_category = payload.category or "Uncategorized"
    kind, category, counterparty = _resolve_kind(payload.kind, payload.direction, label, payload.category, fallback_category)

    # Backdating, for logging something after the fact — cash from yesterday,
    # or a payment no bank ever alerted about. Refused for the future, since
    # that would put spending in a month that hasn't happened; `ingested_at`
    # stays "now" regardless, so dedupe and audit order are unaffected.
    occurred_at = None
    if payload.occurred_on:
        y, m, d = (int(part) for part in payload.occurred_on.split("-"))
        try:
            occurred_at = local_date_to_utc(y, m, d)
        except ValueError as exc:
            raise HTTPException(422, "Not a real date") from exc
        if occurred_at > utc_now_naive() + timedelta(days=1):
            raise HTTPException(422, "That date is in the future")

    txn = Transaction(
        merchant=label if payload.kind != "expense" else payload.merchant,
        amount=payload.amount,
        direction=payload.direction,
        category=category,
        source="manual",
        needs_review=False,
        note=(payload.note or "").strip() or None,
        kind=kind,
        counterparty=counterparty,
        **({"created_at": occurred_at} if occurred_at else {}),
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


@api.post("/transactions/screenshot-import")
def screenshot_import(payload: ScreenshotImport, db: Session = Depends(get_db)):
    """Book a whole screenshot's worth of confirmed rows in one go.

    Two things this does that looping over /transactions/manual did not:

    1. One request, one commit. The client used to POST each row separately,
       so a six-row screenshot was six round trips to a single-worker free
       backend, and a failure partway left half a screenshot imported with no
       record of which half.
    2. Tags every row with `source="screenshot"` and a shared batch id. Before
       this, imported rows were indistinguishable from ones typed by hand, so
       there was no way to see what an upload brought in and no way to undo
       one — which matters because this is a daily habit and the rows come
       from a vision model reading a photo.
    """
    batch = uuid.uuid4().hex[:12]
    created = []

    for row in payload.rows:
        label = row.merchant or ("Someone" if row.kind in ("friend", "friend_settle") else "Transaction")

        # Backstop for the client sending a self-transfer as an ordinary
        # expense. It did exactly that once and booked a GPay row literally
        # labelled "Self transfer" as Rs 10,000 of spending — money moved
        # between the owner's own accounts, counted as a purchase, inflating
        # the month by a fifth. The client is fixed too; this makes the same
        # mistake unrepeatable from any caller.
        kind_choice = row.kind
        if kind_choice == "expense" and row.category == "Transfer":
            kind_choice = "self"

        kind, category, counterparty = _resolve_kind(
            kind_choice, row.direction, label, row.category, row.category or "Uncategorized"
        )

        occurred_at = None
        if row.occurred_on:
            y, m, d = (int(part) for part in row.occurred_on.split("-"))
            try:
                occurred_at = local_date_to_utc(y, m, d)
            except ValueError as exc:
                raise HTTPException(422, f"Not a real date: {row.occurred_on}") from exc
            if occurred_at > utc_now_naive() + timedelta(days=1):
                raise HTTPException(422, f"{row.occurred_on} is in the future")

        txn = Transaction(
            merchant=label,
            amount=row.amount,
            direction=row.direction,
            category=category,
            source="screenshot",
            needs_review=False,
            kind=kind,
            counterparty=counterparty,
            import_batch=batch,
            **({"created_at": occurred_at} if occurred_at else {}),
        )
        db.add(txn)
        created.append(txn)

    db.commit()
    return {
        "batch": batch,
        "added": len(created),
        # Only what actually counts as spending — reporting the raw sum would
        # include a self-transfer and overstate what was just added.
        "total": round(sum(t.amount for t in created if t.kind == "expense"), 2),
    }


@api.get("/imports")
def list_imports(limit: int = Query(default=10, ge=1, le=50), db: Session = Depends(get_db)):
    """Recent screenshot imports, newest first — what each one brought in, so
    a bad upload can be found and undone."""
    rows = (
        db.query(
            Transaction.import_batch,
            func.count(Transaction.id),
            func.sum(Transaction.amount),
            func.max(Transaction.ingested_at),
        )
        .filter(Transaction.import_batch.isnot(None))
        .group_by(Transaction.import_batch)
        .all()
    )
    batches = [
        {
            "batch": batch,
            "count": int(count or 0),
            "total": round(float(total or 0), 2),
            "at": at.isoformat() + "Z" if at else None,
        }
        for batch, count, total, at in rows
    ]
    batches.sort(key=lambda b: b["at"] or "", reverse=True)
    return batches[:limit]


@api.delete("/imports/{batch}")
def undo_import(batch: str, db: Session = Depends(get_db)):
    """Undo one screenshot import completely.

    Deletes only rows carrying this batch id, so anything logged by hand or
    captured from a bank alert in between is untouched — the reason the batch
    id exists at all.
    """
    rows = db.query(Transaction).filter(Transaction.import_batch == batch).all()
    if not rows:
        raise HTTPException(404, "No import with that id (already undone?)")
    for txn in rows:
        db.delete(txn)
    db.commit()
    return {"status": "undone", "batch": batch, "removed": len(rows)}


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


MERCHANT_STOPWORDS_FOR_MATCH = {
    "the", "and", "pvt", "ltd", "limited", "private", "india", "co", "company",
    "services", "service", "store", "shop", "payments", "payment", "online",
}


def _match_key(name: str) -> set[str]:
    """Distinctive lowercase word-ish tokens from a merchant name.

    The same payment is named differently by every source — GPay says
    "Republic Petroleum Station and Co" where the bank alert says
    "paytm-30139373@ptys" — so exact name matching is useless. Shared
    distinctive tokens are what actually survive between them.
    """
    cleaned = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    return {
        w for w in cleaned.split()
        if len(w) >= 4 and w not in MERCHANT_STOPWORDS_FOR_MATCH and not w.isdigit()
    }


def _flag_duplicates(rows: list[dict], db: Session) -> None:
    """Mark each scanned row that looks like something already recorded, or
    like a repeat of an earlier row in the SAME screenshot.

    Deliberately errs towards flagging. An unticked row the user re-ticks
    costs one tap; a silently double-counted payment corrupts every total it
    touches and is very hard to notice later. Three ways a row gets flagged:

    1. It repeats an earlier row in this same scan. Reading one line twice is
       a real failure mode of the vision model, and it happened: a screenshot
       produced two identical "Amazon Pay Gift Card Rs 232" rows when only one
       such payment existed.
    2. Same amount on the same day as an existing transaction — any
       direction. Restricting this to debits previously let an already-stored
       credit through unflagged.
    3. Same amount within a day either side, AND a shared distinctive word in
       the merchant name. Sources disagree about dates near midnight and about
       names, so neither alone is enough, but together they are convincing.
    """
    existing = db.query(Transaction).all()
    by_amount: dict[float, list] = {}
    for t in existing:
        by_amount.setdefault(round(t.amount, 2), []).append(
            (local_day_key(t.created_at), _match_key(t.merchant or ""))
        )

    seen_in_scan: dict[tuple, int] = {}
    for index, row in enumerate(rows):
        amount = round(row["amount"], 2)
        date = row.get("occurred_on")
        tokens = _match_key(row.get("merchant") or "")
        reason = None

        scan_key = (amount, date, tuple(sorted(tokens)))
        if scan_key in seen_in_scan:
            reason = "appears twice in this screenshot"
        else:
            seen_in_scan[scan_key] = index
            for stored_day, stored_tokens in by_amount.get(amount, []):
                if date and stored_day == date:
                    reason = "already recorded"
                    break
                if date and tokens and (tokens & stored_tokens) and _within_a_day(date, stored_day):
                    reason = f"already recorded on {stored_day}"
                    break

        row["already_recorded"] = reason is not None
        row["duplicate_reason"] = reason


def _within_a_day(a: str, b: str) -> bool:
    """True when two YYYY-MM-DD strings are at most one day apart."""
    try:
        da = date_cls.fromisoformat(a)
        db_ = date_cls.fromisoformat(b)
    except ValueError:
        return False
    return abs((da - db_).days) <= 1


@api.post("/ai/scan-statement")
async def scan_statement_endpoint(
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Reads a screenshot of a transaction LIST (GPay/PhonePe history, a bank
    statement) and returns every row found, each flagged with whether it
    already exists here.

    Necessary because alert-based capture is demonstrably incomplete — a real
    GPay history showed six payments totalling Rs 9,691 that the bank never
    emailed or texted. None of these apps expose an API, so a screenshot is
    the only machine-readable form of that data available.

    Returns a preview; books nothing. The `already_recorded` flag is what
    makes the screenshot safely re-uploadable: without it, importing the same
    history twice would silently double every figure.
    """
    if not gemini_configured():
        raise HTTPException(503, "Screenshot scanning isn't set up (no GEMINI_API_KEY on the server)")

    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(422, "Empty image")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image too large (max 8MB)")

    try:
        rows = await asyncio.to_thread(
            scan_statement, image_bytes, image.content_type or "image/jpeg", local_now().year
        )
    except ReceiptScanError as e:
        raise HTTPException(502, str(e)) from e
    except Exception as e:  # noqa: BLE001 — same backstop as /ai/scan-receipt
        raise HTTPException(502, f"Couldn't read that screenshot: {e}") from e

    if not rows:
        return {"transactions": []}

    _flag_duplicates(rows, db)
    return {"transactions": rows}


@api.get("/stats/summary")
def stats_summary(
    period: str = Query(default="month", pattern="^(day|week|month)$"),
    db: Session = Depends(get_db),
):
    """Day / week / month review: what was spent, how that compares with the
    period before, where it went, and who to.

    Every figure filters on `kind == "expense"`, never on direction — money
    lent to a friend or loaded onto a wallet is a debit but not spending, and
    a review screen that conflated them would be lying about the number people
    look at most (see the money-movement kinds note in CLAUDE.md).
    """
    def spend_between(start, end):
        rows = (
            db.query(Transaction)
            .filter(
                Transaction.kind == "expense",
                Transaction.created_at >= start,
                Transaction.created_at < end,
            )
            .all()
        )
        return rows

    start, end = period_range_utc(period, 0)
    prev_start, prev_end = period_range_utc(period, 1)

    current = spend_between(start, end)
    previous = spend_between(prev_start, prev_end)

    total = round(sum(t.amount for t in current), 2)
    prev_total = round(sum(t.amount for t in previous), 2)

    # No percentage when there's nothing to compare against: "+100%" against a
    # zero baseline reads as a real trend when it only means "last time was 0".
    delta_pct = round((total - prev_total) / prev_total * 100, 1) if prev_total > 0 else None

    by_category: dict[str, float] = {}
    by_merchant: dict[str, dict] = {}
    for t in current:
        by_category[t.category] = by_category.get(t.category, 0) + t.amount
        name = (t.merchant or "Unknown").strip() or "Unknown"
        entry = by_merchant.setdefault(name, {"merchant": name, "spent": 0.0, "count": 0})
        entry["spent"] += t.amount
        entry["count"] += 1

    income = (
        db.query(func.coalesce(func.sum(Transaction.amount), 0.0))
        .filter(
            Transaction.kind == "income",
            Transaction.created_at >= start,
            Transaction.created_at < end,
        )
        .scalar()
    )

    return {
        "period": period,
        "label": period_label(period, 0),
        "previous_label": period_label(period, 1),
        "start": start.isoformat() + "Z",
        "end": end.isoformat() + "Z",
        "total_spent": total,
        "previous_spent": prev_total,
        "delta_pct": delta_pct,
        "total_income": round(float(income or 0), 2),
        "transaction_count": len(current),
        "categories": sorted(
            ({"category": c, "spent": round(v, 2)} for c, v in by_category.items()),
            key=lambda x: -x["spent"],
        ),
        "merchants": sorted(
            ({**m, "spent": round(m["spent"], 2)} for m in by_merchant.values()),
            key=lambda x: -x["spent"],
        )[:8],
    }


# How many days past its usual date a recurring payment may drift before it is
# called overdue rather than simply upcoming. Rent lands on the 3rd one month
# and the 5th the next; nobody wants that reported as a missed payment.
RECURRING_GRACE_DAYS = 3


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


@api.get("/stats/recurring")
def recurring_expectations(
    months_back: int = Query(default=6, ge=2, le=24),
    db: Session = Depends(get_db),
):
    """Payments that repeat every month — and, for each, whether this month's
    has actually happened yet.

    Detecting recurrence was already possible client-side, but it only ever
    described the past: "you pay this every month" is a fact, not a prompt.
    The useful half is the one this adds — the maid's salary, the gym fee, the
    rent, each either ticked off or still outstanding with the date it usually
    lands on. That is the difference between a record and something that tells
    you a payment has been forgotten.

    Median rather than mean throughout: one unusually large grocery run should
    not drag the expected amount up, and one payment made early on the 1st
    should not drag the expected date to the start of the month.
    """
    now = local_now()
    year, month = now.year, now.month
    for _ in range(months_back):
        month -= 1
        if month < 1:
            month, year = 12, year - 1
    window_start, _ = month_range_utc(year, month)

    rows = (
        db.query(Transaction)
        .filter(Transaction.kind == "expense", Transaction.created_at >= window_start)
        .all()
    )

    groups: dict[tuple[str, str], dict] = {}
    for txn in rows:
        name = (txn.merchant or "").strip()
        if not name or name.lower() == "unknown":
            continue
        key = (name.lower(), txn.category)
        day_key = local_day_key(txn.created_at)
        entry = groups.setdefault(key, {
            "label": name, "category": txn.category, "months": set(),
            "amounts": [], "days": [], "last_seen": day_key,
        })
        entry["months"].add(day_key[:7])
        entry["amounts"].append(float(txn.amount))
        # Rows bulk-loaded from a spreadsheet only ever knew a month, so they
        # are anchored to the 1st (see /transactions/import). They are real
        # evidence that the month happened, but their "day" is an artefact —
        # counting it would drag every expected date towards the 1st.
        if txn.source != "import":
            entry["days"].append(int(day_key[-2:]))
        entry["label"] = name if day_key >= entry["last_seen"] else entry["label"]
        entry["last_seen"] = max(entry["last_seen"], day_key)

    this_month = f"{now.year:04d}-{now.month:02d}"
    today = now.day
    out = []

    for entry in groups.values():
        if len(entry["months"]) < 2:
            continue
        paid = this_month in entry["months"]
        # Months other than the current one — the current one is what's being
        # predicted, so including a part-finished month would let a payment
        # already made this month inform its own expected date.
        past_months = len(entry["months"]) - (1 if paid else 0)
        if past_months < 2:
            continue

        typical_day = int(round(_median(entry["days"]))) if entry["days"] else None
        if paid:
            status, days_until = "paid", None
        elif typical_day is None:
            status, days_until = "due", None
        else:
            days_until = typical_day - today
            status = "overdue" if days_until < -RECURRING_GRACE_DAYS else "due"

        out.append({
            "merchant": entry["label"],
            "category": entry["category"],
            "typical_amount": round(_median(entry["amounts"]), 2),
            "typical_day": typical_day,
            "months": len(entry["months"]),
            "last_seen": entry["last_seen"],
            "status": status,
            "days_until": days_until,
        })

    # Overdue first — that is the only part of this list that needs acting on.
    order = {"overdue": 0, "due": 1, "paid": 2}
    out.sort(key=lambda r: (order[r["status"]], r["days_until"] if r["days_until"] is not None else 99, -r["typical_amount"]))
    return out


@api.get("/stats/capture-health")
def capture_health(db: Session = Depends(get_db)):
    """Is automatic capture still alive?

    Answers the question nothing else in the app could: bank alerts simply
    stopping produces no error anywhere — the totals just quietly stop
    growing, and every screen keeps looking correct. See CAPTURE_QUIET_HOURS.

    Measured on `ingested_at` (when the alert reached us), never `created_at`
    (when the bank says the payment happened). Those are different by design,
    and back-dated alerts make created_at useless for "have we heard anything
    lately" — an email arriving today about last Tuesday would read as five
    days of silence.

    A channel that has never delivered anything is not reported as broken:
    Gmail polling was set up long after SMS forwarding, and calling a pipe
    that was never connected "quiet" is noise, not a warning.
    """
    now = utc_now_naive()

    rows = (
        db.query(Transaction.source, func.max(Transaction.ingested_at))
        .filter(Transaction.source.in_(AUTOMATED_SOURCES))
        .group_by(Transaction.source)
        .all()
    )

    channels = []
    latest = None
    latest_source = None
    for source, last_at in rows:
        if last_at is None:
            continue
        hours = round((now - last_at).total_seconds() / 3600, 1)
        channels.append({
            "source": source,
            "last_at": last_at.isoformat() + "Z",
            "hours_since": hours,
            "quiet": hours >= CAPTURE_QUIET_HOURS,
        })
        if latest is None or last_at > latest:
            latest, latest_source = last_at, source

    channels.sort(key=lambda c: c["hours_since"])

    if latest is None:
        # Nothing has ever been captured automatically. Not an outage —
        # there is no pipe to have broken yet.
        return {
            "quiet": False,
            "threshold_hours": CAPTURE_QUIET_HOURS,
            "hours_since_last": None,
            "last_at": None,
            "last_source": None,
            "channels": [],
            "manual_since": 0,
        }

    hours_since = round((now - latest).total_seconds() / 3600, 1)

    # Transactions the user typed in *since* the last automatic one. This is
    # the tell that separates "quiet because you haven't spent anything" from
    # "quiet because the pipe is dead": still spending, still logging it by
    # hand, and not one alert came in on its own.
    manual_since = (
        db.query(func.count(Transaction.id))
        .filter(
            Transaction.source.notin_(AUTOMATED_SOURCES),
            Transaction.ingested_at > latest,
        )
        .scalar()
    ) or 0

    return {
        "quiet": hours_since >= CAPTURE_QUIET_HOURS,
        "threshold_hours": CAPTURE_QUIET_HOURS,
        "hours_since_last": hours_since,
        "last_at": latest.isoformat() + "Z",
        "last_source": latest_source,
        "channels": channels,
        "manual_since": int(manual_since),
    }


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
    rows = (
        db.query(Transaction)
        .filter(Transaction.needs_review.is_(True))
        .order_by(Transaction.created_at.desc())
        .all()
    )
    # Attach the business hint here rather than storing it: it is derived from
    # the merchant name and UPI id, both of which can improve later, and a
    # stored copy would go stale. Only the review queue needs it — it is the
    # one place the shop/person answer is still open.
    return [
        TransactionOut.model_validate(t).model_copy(
            update={"business_hint": business_hint(t.merchant or "", t.payee_key)}
        )
        for t in rows
    ]


def _refresh_merchant_names(db: Session) -> int:
    """Re-derive whatever the parser can now read but couldn't when a row was
    first stored — display names, the bank's reference, the sender of a
    credit. Pure regex, no AI call, so it runs over everything rather than a
    limited batch.

    The reference backfill matters most: `bank_ref` was added as a nullable
    column with no backfill, so every row already in the database had NULL
    and reference-based dedupe could not protect against a second copy of any
    of them. That is exactly how a Rs 10,000 credit ended up stored twice.

    Only touches rows whose merchant still *is* the parsed VPA — that's proof
    it was auto-derived and never edited by hand, so a name the user typed in
    Review can't be clobbered.
    """
    rows = db.query(Transaction).filter(Transaction.raw_text.isnot(None)).all()
    fixed = 0

    # Backfill the bank's reference wherever the alert carries one.
    # The set of taken references is read ONCE. Querying per row instead meant
    # one round trip per transaction — fine at 175 rows, but this runs on
    # every repair pass and would grow into a gateway timeout, which is the
    # same failure that already truncated an earlier bulk pass at 4 of 29.
    taken = {r[0] for r in db.query(Transaction.bank_ref).filter(Transaction.bank_ref.isnot(None)).all()}
    for txn in rows:
        if txn.bank_ref:
            continue
        ref = parse_bank_ref(txn.raw_text or "")
        # Skip a reference another row already claims: bank_ref is UNIQUE, so
        # assigning it twice would abort the whole repair pass. The row keeps
        # a NULL ref and stays visible as the duplicate it is.
        if ref and ref not in taken:
            txn.bank_ref = ref
            taken.add(ref)
            fixed += 1

    # Credits used to store "Unknown" because the sender line was never read.
    for txn in rows:
        if txn.direction != "credit":
            continue
        parsed = parse_sms(txn.raw_text or "")
        better = parsed.get("merchant")
        if not better or better == "Unknown":
            continue
        current = (txn.merchant or "").strip()
        # Only replace a placeholder, never a name the user typed in Review.
        if current in ("", "Unknown", "HDFC Bank account credit"):
            txn.merchant = better
            if parsed.get("upi_id") and not txn.payee_key:
                txn.payee_key = parsed["upi_id"]
            fixed += 1

    # Re-date rows that were stamped with their ingestion time instead of the
    # date the bank gave. Only moves a row BACKWARDS in time (an alert cannot
    # describe the future), and only when the bank's date is a different local
    # day — so a live SMS ingested minutes after it happened is left alone
    # rather than being nudged to local noon for no reason.
    now = utc_now_naive()
    for txn in rows:
        alert_date = parse_alert_date(txn.raw_text or "")
        if not alert_date:
            continue
        candidate = local_date_to_utc(*alert_date)
        if candidate > now or candidate < now - timedelta(days=365):
            continue
        if local_day_key(candidate) == local_day_key(txn.created_at):
            continue
        if candidate >= txn.created_at:
            continue  # never push a transaction forward
        txn.created_at = candidate
        fixed += 1

    for txn in rows:
        parsed = parse_sms(txn.raw_text or "")
        better = parsed.get("merchant")
        upi = parsed.get("upi_id")
        if not better or better == "Unknown" or not upi:
            continue
        if (txn.merchant or "").strip().lower() != upi:
            continue  # human-edited, or already the readable name
        if better.lower() == upi:
            continue  # message carries no better name
        txn.merchant = better
        fixed += 1

    # `counterparty` drives the Lending card's "who owes you what", and it was
    # copied from `merchant` at classify time — so on anything answered before
    # this fix it holds the VPA, and the card lists debts against
    # "scientificmonesh@okhdfcbank" rather than a person's name. Only replaced
    # when it still looks like a raw handle, never when it's already a name.
    for txn in db.query(Transaction).filter(Transaction.counterparty.isnot(None)).all():
        current = (txn.counterparty or "").strip()
        if "@" not in current:
            continue  # already a readable name
        if txn.merchant and "@" not in txn.merchant and txn.merchant != "Unknown":
            txn.counterparty = txn.merchant
            fixed += 1

    # Payee labels were saved from whatever `merchant` held at the time, so
    # answers given before this fix are stored as the VPA too — which is what
    # the Remembered list would show. Same guard as above: only relabel when
    # the stored label still IS the key, proving it was auto-derived rather
    # than typed by the user in Review.
    # One pass to collect the best readable name per payee_key, rather than a
    # query per payee — same round-trip problem as the reference backfill.
    best_name: dict[str, str] = {}
    for txn in sorted(
        db.query(Transaction).filter(Transaction.payee_key.isnot(None)).all(),
        key=lambda t: t.created_at,
        reverse=True,
    ):
        name = (txn.merchant or "").strip()
        if name and "@" not in name and name != "Unknown":
            best_name.setdefault(txn.payee_key, name)

    for payee in db.query(Payee).all():
        if (payee.label or "").strip().lower() != payee.key.lower():
            continue
        better = best_name.get(payee.key)
        if better:
            payee.label = better
            fixed += 1

    if fixed:
        db.commit()
    return fixed


def _recategorize_pending_sync(db: Session, limit: int) -> dict:
    # Names first, and for every row, not just this batch: it's free (regex,
    # no API call) and the AI pass immediately below is only as good as the
    # merchant string it's handed — categorising "q743985996@ybl" is
    # hopeless, categorising "Ss Hyderabad Biriyani Peravallur" is trivial.
    renamed = _refresh_merchant_names(db)

    # Only still-Uncategorized rows, so repeated small batches naturally
    # pick up where the last one left off instead of reprocessing anything
    # already fixed — needed because Render's own gateway kills a request
    # around ~30s regardless of the backend still working (confirmed live:
    # a single unbatched call over the whole backlog returned a 502 to the
    # client after only 4 of 29 actually got updated server-side).
    pending = (
        db.query(Transaction)
        .filter(
            Transaction.needs_review.is_(True),
            Transaction.direction == "debit",
            Transaction.category == "Uncategorized",
        )
        .limit(limit)
        .all()
    )
    updated = 0
    for i, txn in enumerate(pending):
        if i > 0:
            time.sleep(1)
        result = categorize(txn.merchant or "", txn.raw_text or "", txn.direction)
        if result["source"] == "ai_confident" and result["category"] != txn.category:
            txn.category = result["category"]
            updated += 1
    db.commit()
    return {"checked": len(pending), "updated": updated, "renamed": renamed}


@api.post("/transactions/recategorize-pending")
async def recategorize_pending(limit: int = Query(default=8, ge=1, le=50), db: Session = Depends(get_db)):
    """Maintenance action: re-runs categorize() against transactions still
    sitting in Review with category=Uncategorized, and updates just the
    category field when a confident AI result comes back — needs_review is
    left untouched, so the shop/person/wallet/my account question still
    gets asked, just with a correct category pre-filled in Review's chip
    picker instead of always defaulting to Food & Dining. `limit` keeps a
    single call well under Render's own gateway timeout; call repeatedly
    (the Uncategorized filter makes it safe to re-call — always picks up
    wherever the last call left off) to work through a larger backlog."""
    return await asyncio.to_thread(_recategorize_pending_sync, db, limit)


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
    if payload.note is not None:
        txn.note = payload.note.strip() or None
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


@api.get("/payees", response_model=list[PayeeOut])
def list_payees(db: Session = Depends(get_db)):
    """Everything remembered from a 'who is this?' answer — shop, person,
    wallet, or your own account. Read-only visibility into memory that was
    previously invisible: it silently drove classification (new sightings
    of the same payee skip Review) but had no way to actually be seen."""
    payees = db.query(Payee).order_by(Payee.created_at.desc()).all()
    counts = dict(
        db.query(Transaction.payee_key, func.count(Transaction.id))
        .filter(Transaction.payee_key.isnot(None))
        .group_by(Transaction.payee_key)
        .all()
    )
    return [
        PayeeOut.model_validate(p).model_copy(update={
            # Only questioned on answers of "a person". A shop remembered as a
            # shop needs no second-guessing, and flagging those too would bury
            # the one case that matters in noise.
            "business_hint": (
                business_hint(p.label, p.key) if p.kind in ("friend", "friend_settle") else None
            ),
            "used_by": counts.get(p.key, 0),
        })
        for p in payees
    ]


@api.post("/lending/{person:path}/repaid", response_model=TransactionOut)
def record_repayment(
    person: str,
    amount: float | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
):
    """Record that someone paid you back OUTSIDE the bank — in cash, most
    often. Without this the Lending card can only ever see repayments that
    happened to arrive as a bank credit, so a debt settled in cash stays on
    the list forever and the reminder keeps nagging about money already back
    in your pocket.

    Books a real `repayment` row (not a deletion of the original loan) so the
    history stays truthful: you lent it, they returned it, both happened.
    `repayment` is not income and not spending, so no total moves — it only
    cancels the outstanding balance. Defaults to the full outstanding amount;
    pass `amount` for a partial repayment.
    """
    rows = (
        db.query(Transaction)
        .filter(Transaction.kind.in_(("lend", "repayment")), Transaction.counterparty == person)
        .all()
    )
    if not rows:
        raise HTTPException(404, "No lending history with that person")

    outstanding = sum(t.amount if t.kind == "lend" else -t.amount for t in rows)
    if outstanding <= 0:
        raise HTTPException(409, "Nothing outstanding with that person")

    value = amount if amount is not None else outstanding
    if value > outstanding:
        raise HTTPException(422, f"That's more than the {outstanding:.2f} outstanding")

    txn = Transaction(
        merchant=person,
        amount=value,
        direction="credit",
        category="Lending",
        source="manual",
        needs_review=False,
        kind="repayment",
        counterparty=person,
    )
    db.add(txn)

    # Fully settled — stop the reminder rather than leaving it to fire about
    # a debt that no longer exists.
    if abs(value - outstanding) < 0.01:
        reminder = db.get(LendingReminder, person)
        if reminder:
            db.delete(reminder)

    db.commit()
    db.refresh(txn)
    return txn


@api.patch("/payees/{key:path}")
def update_payee(key: str, payload: PayeeUpdate, db: Session = Depends(get_db)):
    """Correct a remembered 'who is this?' answer — and, optionally, every
    transaction it already decided.

    DELETE (below) only stops an answer being applied in future. That is not
    enough when the answer was wrong: a bakery answered as "a person" had its
    payments filed as money lent out, sat in the who-owes-you list next to
    real friends, and — because a remembered payee is never asked about again
    — would have kept doing so silently forever. Forgetting it would fix the
    next payment while leaving all the wrong ones in place.

    `apply_to_past` re-runs each affected transaction through _resolve_kind()
    with the corrected answer, which is the same function that classified it
    in the first place, so the two can't disagree about what "friend" means.
    Off by default: it moves historical totals, and that should be a choice.
    """
    payee = db.get(Payee, key)
    if not payee:
        raise HTTPException(404, "Not remembered")

    label = (payload.label or payee.label).strip() or payee.label
    payee.label = label
    payee.kind = payload.kind
    # A default category is only meaningful for a shop. Carrying "Lending"
    # over onto a corrected answer would re-apply the very mistake being fixed.
    payee.default_category = payload.category if payload.kind == "expense" else None

    updated = 0
    if payload.apply_to_past:
        rows = db.query(Transaction).filter(Transaction.payee_key == key).all()
        for txn in rows:
            # The old category is only a sane fallback if it isn't itself a
            # product of the wrong answer. "Lending" and "Transfer" are
            # markers of a non-spending kind, so keeping one on a row now
            # being re-filed as an ordinary purchase would leave the number
            # right and the label nonsense. Ask instead of guessing.
            stale = txn.category in ("Lending", "Transfer")
            fallback = "Uncategorized" if stale else txn.category
            txn.kind, txn.category, txn.counterparty = _resolve_kind(
                payload.kind, txn.direction, label, payload.category, fallback
            )
            txn.merchant = label
            if payload.kind == "expense" and txn.category == "Uncategorized":
                txn.needs_review = True
            updated += 1

        # A person who turns out to be a shop has no debt to be nudged about.
        # Left behind, the reminder keeps firing about money that was never
        # lent to anyone.
        for person in {payee.label} | {r.counterparty for r in rows if r.counterparty}:
            still_lending = (
                db.query(Transaction)
                .filter(
                    Transaction.kind.in_(("lend", "repayment")),
                    Transaction.counterparty == person,
                )
                .first()
            )
            reminder = db.get(LendingReminder, person)
            if reminder and not still_lending:
                db.delete(reminder)

    db.commit()
    db.refresh(payee)
    return {
        "payee": PayeeOut.model_validate(payee).model_copy(update={
            "business_hint": (
                business_hint(payee.label, payee.key)
                if payee.kind in ("friend", "friend_settle") else None
            ),
        }),
        "updated": updated,
    }


@api.delete("/payees/{key:path}")
def forget_payee(key: str, db: Session = Depends(get_db)):
    """Forget one remembered answer, so the next transaction from that
    counterparty asks again. Without this a mis-tap in Review is permanent
    and silently keeps mis-filing every future payment from that payee.

    `{key:path}` because keys are UPI ids and `name:<merchant>` strings —
    both contain characters a plain path segment would refuse.

    Deliberately leaves existing transactions alone: they were classified
    correctly as far as the user was concerned at the time, and silently
    rewriting history would move totals under them.
    """
    payee = db.get(Payee, key)
    if not payee:
        raise HTTPException(404, "Not remembered")
    db.delete(payee)
    db.commit()
    return {"status": "forgotten", "key": key}


@api.get("/export/all")
def export_all(db: Session = Depends(get_db)):
    """Everything, as plain JSON — the whole database in one response.

    All of this lives in a single free-tier Postgres project with no backup of
    any kind, and rows have been deleted by hand more than once while
    correcting double-counted months. Nightly snapshots are cheap insurance;
    see .github/workflows/backup.yml, which encrypts the result before storing
    it, because this repository is public.

    Deliberately excludes the gmail_auth table. It holds a Google OAuth
    refresh token — a live credential, not data — and a backup file is exactly
    the wrong place for one. Re-connecting Gmail after a restore is a single
    visit to /gmail/auth/start; leaking a token that never expires is not
    undoable.
    """
    def dump(model):
        return [
            {
                column.name: (
                    value.isoformat() + "Z" if isinstance(value, datetime_cls) else value
                )
                for column in model.__table__.columns
                for value in (getattr(obj, column.name),)
            }
            for obj in db.query(model).all()
        ]

    tables = {
        "transactions": dump(Transaction),
        "budgets": dump(Budget),
        "payees": dump(Payee),
        "vehicles": dump(Vehicle),
        "fuel_fills": dump(FuelFill),
        "todos": dump(Todo),
        "lending_reminders": dump(LendingReminder),
    }
    return {
        "exported_at": utc_now_naive().isoformat() + "Z",
        "counts": {name: len(rows) for name, rows in tables.items()},
        **tables,
    }


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
    """km/L is only derived between two CONSECUTIVE full-tank fills — a
    partial fill at either end is skipped rather than producing a fabricated
    number, because only a full-to-full interval guarantees the litres put in
    equal the litres burned over that distance.

    Distance comes from whichever the user actually records, and the two
    differ in how much they need:

    * `trip_km` is self-contained — ONE full-tank fill is enough, because a
      trip meter reset at the previous fill already encodes the distance that
      tankful covered.
    * an odometer is only a position, so it needs the previous full tank's
      reading to subtract — that genuinely requires two fills.

    A non-positive distance (odometer reset, mistyped entry) is skipped.
    """
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

    # Two different shapes of leg, because the two ways of recording distance
    # carry different amounts of information:
    #
    # * trip_km is SELF-CONTAINED. Resetting the trip at the last fill means
    #   its reading now *is* the distance that tankful covered, and the litres
    #   going in now are what replaced it — so a single full-tank fill gives
    #   km/L on its own, with no earlier row needed. Demanding a second fill
    #   here (the odometer rule, wrongly applied) is why a perfectly complete
    #   entry — 7.94 L, 186.9 km trip — still reported no mileage at all.
    #   It does assume the tank was full when the trip was reset, which is
    #   what "reset it at every fill-up" means in practice.
    #
    # * An odometer reading is only a position, so it means nothing without
    #   the previous one to subtract — that genuinely needs two full tanks.
    legs = []
    prev_full = None
    for f in fills:
        if not f.is_full_tank:
            # A partial fill is not an endpoint: the litres put in don't
            # correspond to a full tank's worth of driving.
            continue
        km = None
        from_id = None
        if f.trip_km and f.trip_km > 0:
            km = f.trip_km
        elif prev_full is not None and f.odometer is not None and prev_full.odometer is not None:
            km = f.odometer - prev_full.odometer
            from_id = prev_full.id
        prev_full = f
        if not km or km <= 0 or not f.liters:
            continue  # odometer reset, missing litres, or bad entry
        legs.append({
            "from_fill_id": from_id,
            "to_fill_id": f.id,
            "km": km,
            "liters": f.liters,
            "km_per_liter": km / f.liters,
            "cost_per_km": f.amount / km,
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

"""End-to-end smoke test against an in-memory copy of the API.

    python smoke_test.py

Uses a throwaway SQLite file and a fake token, so it never touches budget.db
and never calls the Anthropic API (unrecognised merchants just land in review).
"""

import os
import pathlib
import sys
import tempfile

# Windows' default console codepage (cp1252) can't print ₹ — force UTF-8 so
# this runs the same on a Windows dev box and the Linux backend it deploys to.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

TMP_DB = pathlib.Path(tempfile.gettempdir()) / "budget_smoke.db"
TMP_DB.unlink(missing_ok=True)

os.environ["AUTH_TOKEN"] = "smoke-test-token"
os.environ["DATABASE_URL"] = f"sqlite:///{TMP_DB.as_posix()}"
os.environ.pop("ANTHROPIC_API_KEY", None)  # force the no-AI path
os.environ.setdefault("TZ_NAME", "Asia/Kolkata")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from categorizer import parse_sms  # noqa: E402

client = TestClient(main.app)
AUTH = {"Authorization": "Bearer smoke-test-token"}
failures = []


def check(label, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {label}{'' if condition else f'  -> {detail}'}")
    if not condition:
        failures.append(label)


# --- auth ---------------------------------------------------------------------
check("health is public", client.get("/health").status_code == 200)
check("no token -> 401", client.get("/transactions").status_code == 401)
check("bad token -> 401", client.get("/transactions", headers={"Authorization": "Bearer nope"}).status_code == 401)
check("good token -> 200", client.get("/me", headers=AUTH).status_code == 200)

# --- SMS ingestion ------------------------------------------------------------
sms_swiggy = "Rs.499.00 debited from A/c XX1234 on 12-05-24 to VPA swiggy@icici (UPI Ref 402913)"
r = client.post("/sms/ingest", json={"text": sms_swiggy}, headers=AUTH).json()
check("swiggy SMS booked", r["status"] == "ok", r)
check("amount parsed", r["transaction"]["amount"] == 499.0, r["transaction"])
check("direction=debit", r["transaction"]["direction"] == "debit", r["transaction"])
check("rule categorised as Food & Dining", r["transaction"]["category"] == "Food & Dining", r["transaction"])
check("rule match is confident", r["transaction"]["needs_review"] is False, r["transaction"])

r = client.post("/sms/ingest", json={"text": sms_swiggy}, headers=AUTH).json()
check("identical SMS deduped", r["status"] == "duplicate", r)

r = client.post("/sms/ingest", json={"text": "894213 is your OTP for a transaction of Rs 2000. Do not share."}, headers=AUTH).json()
check("OTP ignored", r["status"] == "ignored", r)

r = client.post("/sms/ingest", json={"text": "Congratulations! You are pre-approved for a loan of Rs 5,00,000. Apply now!"}, headers=AUTH).json()
check("promo ignored", r["status"] == "ignored", r)

r = client.post("/sms/ingest", json={"text": "INR 1,250.50 spent on HDFC Card x1234 at MADHURA SWEETS on 2026-08-15"}, headers=AUTH).json()
check("unknown merchant -> needs review", r["transaction"]["needs_review"] is True, r["transaction"])
check("merchant extracted", r["transaction"]["merchant"] == "MADHURA SWEETS", r["transaction"])
check("comma amount parsed", r["transaction"]["amount"] == 1250.50, r["transaction"])
review_id = r["transaction"]["id"]

r = client.post("/sms/ingest", json={"text": "Rs 45000 credited to A/c XX1234 by salary transfer from ACME CORP"}, headers=AUTH).json()
check("credit direction detected", r["transaction"]["direction"] == "credit", r["transaction"])
check("credit auto-filed as Income", r["transaction"]["category"] == "Income", r["transaction"])
check("credit skips the review queue", r["transaction"]["needs_review"] is False, r["transaction"])

r = client.post("/sms/ingest", json={"text": "Rs 320 debited for UBER INDIA on 15-08-26"}, headers=AUTH).json()
check("uber -> Transport", r["transaction"]["category"] == "Transport", r["transaction"])
check("'debited for X' merchant parsed", r["transaction"]["merchant"] == "UBER INDIA", r["transaction"])

# The amount must never be mistaken for the merchant name.
p = parse_sms("Your a/c XX123 is debited for Rs 1000 towards NETFLIX on 03-08-26")
check("currency token not read as merchant", p["merchant"] == "NETFLIX", p)
p = parse_sms("Rs.499.00 debited from A/c XX1234 to VPA swiggy@icici (UPI Ref 402913)")
check("VPA handle preferred over boilerplate", p["merchant"] == "swiggy@icici", p)
p = parse_sms("INR 1,250.50 spent on HDFC Card x1234 at MADHURA SWEETS on 2026-08-15")
check("'at X on' merchant parsed", p["merchant"] == "MADHURA SWEETS", p)

# --- manual entry -------------------------------------------------------------
r = client.post("/transactions/manual", json={"amount": 2000, "direction": "debit", "category": "Rent", "merchant": "Landlord"}, headers=AUTH)
check("manual expense created", r.status_code == 200, r.text)
manual_id = r.json()["id"]

bad = client.post("/transactions/manual", json={"amount": -5, "direction": "debit", "category": "Rent"}, headers=AUTH)
check("negative amount rejected", bad.status_code == 422, bad.text)
bad = client.post("/transactions/manual", json={"amount": 5, "direction": "debit", "category": "Nonsense"}, headers=AUTH)
check("unknown category rejected", bad.status_code == 422, bad.text)

# --- review queue -------------------------------------------------------------
queue = client.get("/transactions/needs-review", headers=AUTH).json()
check("review queue has the unknown merchant", any(t["id"] == review_id for t in queue), queue)

r = client.patch(f"/transactions/{review_id}/category", json={"category": "Food & Dining"}, headers=AUTH).json()
check("recategorise works", r["category"] == "Food & Dining", r)
check("recategorise clears the flag", r["needs_review"] is False, r)
queue = client.get("/transactions/needs-review", headers=AUTH).json()
check("review queue now empty", queue == [], queue)
check("patching a missing txn -> 404", client.patch("/transactions/99999/category", json={"category": "Rent"}, headers=AUTH).status_code == 404)

# --- budgets ------------------------------------------------------------------
client.post("/budget/set", json={"category": "Food & Dining", "monthly_limit": 5000}, headers=AUTH)
client.post("/budget/set", json={"category": "Rent", "monthly_limit": 1000}, headers=AUTH)
client.post("/budget/set", json={"category": "Food & Dining", "monthly_limit": 6000}, headers=AUTH)
limits = client.get("/budget/limits", headers=AUTH).json()
check("budget upsert (no duplicate rows)", len(limits) == 2, limits)
check("budget updated in place", next(b for b in limits if b["category"] == "Food & Dining")["monthly_limit"] == 6000, limits)

summary = client.get("/budget/summary", headers=AUTH).json()
food = next(c for c in summary["categories"] if c["category"] == "Food & Dining")
check("food spend = 499 + 1250.50", food["spent"] == 1749.50, food)
check("remaining computed", food["remaining"] == 6000 - 1749.50, food)
check("percent computed", food["percent_used"] == 29.2, food)
rent = next(c for c in summary["categories"] if c["category"] == "Rent")
check("over-budget category is negative", rent["remaining"] == -1000, rent)
check("over-budget sorts first", summary["categories"][0]["category"] == "Rent", summary["categories"])
transport = next(c for c in summary["categories"] if c["category"] == "Transport")
check("unbudgeted spend still listed", transport["limit"] is None and transport["spent"] == 320, transport)
check("total_spent excludes credits", summary["total_spent"] == 1749.50 + 320 + 2000, summary)
check("income tracked separately", summary["total_income"] == 45000, summary)

client.post("/budget/set", json={"category": "Rent", "monthly_limit": 0}, headers=AUTH)
check("zero limit clears the budget", len(client.get("/budget/limits", headers=AUTH).json()) == 1)

# --- trend + listing ----------------------------------------------------------
trend = client.get("/stats/daily", headers=AUTH).json()
check("trend covers the whole month", len(trend["days"]) in (28, 29, 30, 31), len(trend["days"]))
check("trend totals match spend", round(sum(d["spent"] for d in trend["days"]), 2) == 4069.50, trend)

txns = client.get("/transactions", headers=AUTH).json()
check("all transactions listed (dupes/OTPs not stored)", len(txns) == 5, len(txns))
check("newest first", txns[0]["id"] > txns[-1]["id"], [t["id"] for t in txns])
check("timestamps are UTC-tagged", txns[0]["created_at"].endswith("Z"), txns[0]["created_at"])

empty = client.get("/transactions?month=1&year=2001", headers=AUTH)
check("a month with no data returns []", empty.status_code == 200 and empty.json() == [], empty.text)
check("out-of-range year rejected", client.get("/transactions?year=1999", headers=AUTH).status_code == 422)

# --- delete -------------------------------------------------------------------
check("delete works", client.delete(f"/transactions/{manual_id}", headers=AUTH).status_code == 200)
check("delete is idempotent-safe (404)", client.delete(f"/transactions/{manual_id}", headers=AUTH).status_code == 404)
check("transaction actually gone", len(client.get("/transactions", headers=AUTH).json()) == 4)

check("categories endpoint", client.get("/categories", headers=AUTH).json()[0] == "Food & Dining")

# --- raw-text fallback (for automations that can't JSON-escape a quote) --------
raw = 'Rs 275 debited for SWIGGY order "LUNCH" on 16-08-26'
r = client.post("/sms/ingest/raw", content=raw, headers={**AUTH, "Content-Type": "text/plain"})
check("raw endpoint accepts unescaped quotes", r.status_code == 200 and r.json()["status"] == "ok", r.text)
check("raw endpoint stores the SMS verbatim", r.json()["transaction"]["raw_text"] == raw, r.json())
check("raw endpoint still categorises", r.json()["transaction"]["category"] == "Food & Dining", r.json())
check("empty raw body rejected", client.post("/sms/ingest/raw", content="", headers=AUTH).status_code == 422)
check("raw endpoint needs auth", client.post("/sms/ingest/raw", content=raw).status_code == 401)

# --- payee memory: the "who is this, once" flow -------------------------------
# Snapshot spend/income BEFORE any lending/top-up activity, so the budgeting
# check below can assert an exact before/after delta instead of a fuzzy
# threshold — precise enough to actually catch a leak, not just a gross bug.
summary_before = client.get("/budget/summary", headers=AUTH).json()
spent_before = summary_before["total_spent"]

# A P2P UPI transfer to someone never seen before — should land in review
# asking "who is this", not get silently filed as a plain expense.
sms_p2p = "Rs.2000.00 debited from a/c XXXX1234 on 17-08-26 to VPA arjun123@ybl Ref 812345671"
r = client.post("/sms/ingest", json={"text": sms_p2p}, headers=AUTH).json()
p2p_id = r["transaction"]["id"]
check("unknown P2P transfer needs review (could be lending)", r["transaction"]["needs_review"] is True, r)
check("payee_key captured from the VPA", r["transaction"]["payee_key"] == "arjun123@ybl", r["transaction"])
check("defaults to kind=expense until classified", r["transaction"]["kind"] == "expense", r["transaction"])

# Answer "a person" — should become `lend` (this is a debit) and remember Arjun.
r = client.patch(
    f"/transactions/{p2p_id}/classify",
    json={"kind": "friend", "label": "Arjun", "remember": True},
    headers=AUTH,
).json()
check("classified as lending", r["kind"] == "lend", r)
check("counterparty recorded", r["counterparty"] == "Arjun", r)
check("review flag cleared", r["needs_review"] is False, r)
check("category set to Lending", r["category"] == "Lending", r)

# The SAME person sends money back later — must resolve to `repayment`
# automatically now, with NO review needed, because we remembered them.
sms_p2p_back = "Rs.2000.00 credited to a/c XXXX1234 on 18-08-26 by VPA arjun123@ybl Ref 812345672"
r = client.post("/sms/ingest", json={"text": sms_p2p_back}, headers=AUTH).json()
check("remembered friend's repayment auto-resolved, no review", r["transaction"]["needs_review"] is False, r)
check("repayment kind assigned automatically", r["transaction"]["kind"] == "repayment", r)
check("counterparty auto-filled from memory", r["transaction"]["counterparty"] == "Arjun", r)

# A THIRD, brand-new UPI id sent to as a wallet top-up.
sms_wallet = "Rs.1000.00 debited from a/c XXXX1234 on 17-08-26 to VPA add-money@paytm Ref 812345673"
r = client.post("/sms/ingest", json={"text": sms_wallet}, headers=AUTH).json()
wallet_id = r["transaction"]["id"]
r = client.patch(
    f"/transactions/{wallet_id}/classify",
    json={"kind": "wallet", "label": "Paytm Wallet"},
    headers=AUTH,
).json()
check("wallet top-up classified as topup, not expense", r["kind"] == "topup", r)

# Loading the SAME wallet again should skip review entirely from now on.
sms_wallet2 = "Rs.500.00 debited from a/c XXXX1234 on 19-08-26 to VPA add-money@paytm Ref 812345674"
r = client.post("/sms/ingest", json={"text": sms_wallet2}, headers=AUTH).json()
check("remembered wallet skips review on repeat top-up", r["transaction"]["needs_review"] is False, r)
check("repeat top-up correctly kept out of spending", r["transaction"]["kind"] == "topup", r)

# A card swipe at an unrecognised merchant — no VPA at all, must still get a
# stable payee_key (name:-prefixed) and the same "ask once" treatment.
sms_card = 'Rs.780.00 spent on HDFC Bank Debit Card xx1122 at CORNER STORE on 17-08-26'
r = client.post("/sms/ingest", json={"text": sms_card}, headers=AUTH).json()
card_id = r["transaction"]["id"]
check("card swipe gets a name: payee key (no VPA present)", (r["transaction"]["payee_key"] or "").startswith("name:"), r)
check("unrecognised card merchant needs review", r["transaction"]["needs_review"] is True, r)

r = client.patch(
    f"/transactions/{card_id}/classify",
    json={"kind": "expense", "category": "Shopping", "label": "Corner Store"},
    headers=AUTH,
).json()
check("card swipe classified as a real merchant expense", r["kind"] == "expense" and r["category"] == "Shopping", r)

sms_card2 = 'Rs.250.00 spent on HDFC Bank Debit Card xx1122 at CORNER STORE on 18-08-26 Ref 998877665'
r = client.post("/sms/ingest", json={"text": sms_card2}, headers=AUTH).json()
check("repeat card-swipe merchant recognised, no review", r["transaction"]["needs_review"] is False, r)
check("repeat card-swipe merchant gets its remembered category", r["transaction"]["category"] == "Shopping", r)

# --- kind-aware budgeting: lending/top-ups must NEVER count as spending -------
# Only the two Corner Store swipes (₹780 + ₹250 = ₹1030) are real spending in
# this section — the ₹2000 lend, ₹2000 repayment, and ₹1000+₹500 top-ups must
# contribute exactly zero to total_spent. An exact delta, not a fuzzy
# threshold, so a leak of any size gets caught.
summary = client.get("/budget/summary", headers=AUTH).json()
check(
    "total_spent moved by EXACTLY the two real purchases (₹1030), nothing from lending/top-ups leaked in",
    round(summary["total_spent"] - spent_before, 2) == 1030.0,
    {"before": spent_before, "after": summary["total_spent"]},
)
lending_cat = next((c for c in summary["categories"] if c["category"] == "Lending"), None)
check("Lending category shows zero spend (it's not spending)", lending_cat is None or lending_cat["spent"] == 0, lending_cat)
transfer_cat = next((c for c in summary["categories"] if c["category"] == "Transfer"), None)
check("Transfer category (wallet top-ups) shows zero spend", transfer_cat is None or transfer_cat["spent"] == 0, transfer_cat)

# --- vehicles & fuel mileage ----------------------------------------------------
vehicles = client.get("/vehicles", headers=AUTH).json()
check("three vehicles seeded on first run", len(vehicles) == 3, vehicles)
check("Activa present", any(v["id"] == "activa" for v in vehicles))
check("Speed 400 present", any(v["id"] == "speed400" for v in vehicles))
check("Swift Dzire present", any(v["id"] == "swiftdzire" for v in vehicles))

vehicles2 = client.get("/vehicles", headers=AUTH).json()
check("calling /vehicles again doesn't duplicate the seed", len(vehicles2) == 3, vehicles2)

fill1 = client.post(
    "/fuel/fills",
    json={"vehicle_id": "activa", "amount": 500, "liters": 5, "odometer": 1000, "is_full_tank": True},
    headers=AUTH,
).json()
fill2 = client.post(
    "/fuel/fills",
    json={"vehicle_id": "activa", "amount": 400, "liters": 4, "odometer": 1200, "is_full_tank": True},
    headers=AUTH,
).json()
check("fuel fill recorded", fill1["amount"] == 500.0, fill1)

mileage = client.get("/fuel/mileage?vehicle_id=activa", headers=AUTH).json()
check("mileage computed between two full-tank fills: 200km/4L = 50 km/L", mileage["avg_mileage"] == 50.0, mileage)
check("last_mileage matches avg with only one leg", mileage["last_mileage"] == 50.0, mileage)
check("fuel spend totalled", mileage["total_spent"] == 900.0, mileage)

# A partial fill between two full tanks must not corrupt the leg — it's simply
# not a leg endpoint, so mileage still bridges the two full tanks around it.
client.post(
    "/fuel/fills",
    json={"vehicle_id": "activa", "amount": 150, "liters": 1.5, "odometer": 1100, "is_full_tank": False},
    headers=AUTH,
)
mileage2 = client.get("/fuel/mileage?vehicle_id=activa", headers=AUTH).json()
check("partial fill does not create a spurious extra leg", len(mileage2["legs"]) == 1, mileage2)

check(
    "unknown vehicle rejected",
    client.post("/fuel/fills", json={"vehicle_id": "does-not-exist", "amount": 100, "is_full_tank": True}, headers=AUTH).status_code == 404,
)

del_fill = client.delete(f"/fuel/fills/{fill2['id']}", headers=AUTH)
check("fuel fill deletable", del_fill.status_code == 200, del_fill.text)

# --- to-dos ---------------------------------------------------------------------
t1 = client.post("/todos", json={"text": "Pay credit card bill"}, headers=AUTH).json()
t2 = client.post("/todos", json={"text": "Renew bike insurance"}, headers=AUTH).json()
check("todo created", t1["done"] is False, t1)
check("newer todo sorts first (order is descending-insert)", t2["order"] < t1["order"], (t1, t2))

todos = client.get("/todos", headers=AUTH).json()
check("todos listed", len(todos) == 2, todos)

done = client.patch(f"/todos/{t1['id']}", json={"done": True}, headers=AUTH).json()
check("todo marked done", done["done"] is True, done)
check("completed_at stamped", done["completed_at"] is not None, done)

cleared = client.post("/todos/clear-completed", headers=AUTH).json()
check("clear-completed removes exactly the done one", cleared["cleared"] == 1, cleared)
check("the still-open todo survives", len(client.get("/todos", headers=AUTH).json()) == 1)

# --- lending reminders -----------------------------------------------------------
lending = client.get("/lending", headers=AUTH).json()
arjun = next((p for p in lending if p["person"] == "Arjun"), None)
check("Arjun's lending balance tracked", arjun is not None, lending)
check("lent amount correct", arjun and arjun["lent"] == 2000.0, arjun)
check("repaid amount correct", arjun and arjun["repaid"] == 2000.0, arjun)
check("fully repaid -> zero outstanding", arjun and arjun["outstanding"] == 0.0, arjun)

snooze = client.post("/lending/Arjun/snooze?days=3", headers=AUTH).json()
check("reminder scheduled", snooze["status"] == "ok", snooze)

lending2 = client.get("/lending", headers=AUTH).json()
arjun2 = next((p for p in lending2 if p["person"] == "Arjun"), None)
check("next_reminder_at now set", arjun2 and arjun2["next_reminder_at"] is not None, arjun2)

clear_reminder = client.delete("/lending/Arjun/reminder", headers=AUTH)
check("reminder can be cleared", clear_reminder.status_code == 200, clear_reminder.text)

# --- historical import (month-only dates, e.g. from an old spreadsheet) -------
import_items = [
    {"amount": 500, "category": "Food & Dining", "merchant": "Groceries", "month": 6, "year": 2026},
    {"amount": 300, "category": "Transport", "merchant": "Fuel", "month": 6, "year": 2026},
]
r = client.post("/transactions/import", json={"items": import_items}, headers=AUTH).json()
check("import reports the right count", r["added"] == 2, r)
check("import reports the exact total", r["total"] == 800.0, r)

r2 = client.post("/transactions/import", json={"items": import_items}, headers=AUTH).json()
check("re-running without force is refused, not silently duplicated", r2["status"] == "already_imported", r2)
check("refusal reports the existing count accurately", r2["existing_count"] == 2, r2)

r3 = client.post("/transactions/import", json={"items": import_items, "force": True}, headers=AUTH).json()
check("force=true allows a deliberate second run", r3["added"] == 2, r3)

june_summary = client.get("/budget/summary?month=6&year=2026", headers=AUTH).json()
check(
    "imported rows count toward that month's total (₹800 × 2 runs = ₹1600)",
    june_summary["total_spent"] == 1600.0,
    june_summary,
)

june_trend = client.get("/stats/daily?month=6&year=2026", headers=AUTH).json()
trend_total = sum(d["spent"] for d in june_trend["days"])
check(
    "imported rows are EXCLUDED from the daily trend (would fake-spike the 1st)",
    trend_total == 0.0,
    {"trend_total": trend_total, "days_with_spend": [d for d in june_trend["days"] if d["spent"] > 0]},
)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("All checks passed.")

"""End-to-end smoke test against an in-memory copy of the API.

    python smoke_test.py

Uses a throwaway SQLite file and a fake token, so it never touches budget.db
and never calls the Anthropic API (unrecognised merchants just land in review).
"""

import os
import pathlib
import sys
import tempfile

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

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("All checks passed.")

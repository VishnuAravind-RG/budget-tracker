"""End-to-end smoke test against an in-memory copy of the API.

    python smoke_test.py

Uses a throwaway SQLite file and a fake token, so it never touches budget.db
and never calls the Anthropic API (unrecognised merchants just land in review).
"""

import json
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
from categorizer import parse_alert_date, parse_sms  # noqa: E402

client = TestClient(main.app)
AUTH = {"Authorization": "Bearer smoke-test-token"}
failures = []
passed = []


def check(label, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {label}{'' if condition else f'  -> {detail}'}")
    (passed if condition else failures).append(label)


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

# Dedupe must survive back-dating. Once alerts started being dated by the
# BANK, created_at could sit hours in the past, so a dedupe window measured
# against it matched nothing and every MacroDroid retry on flaky mobile data
# booked the spend twice. This alert carries an explicit date, which is
# exactly the case that broke.
dated = "Rs.777.00 debited from A/c XX1234 on 05-08-26 to VPA dedupetest@ybl Ref 900000001"
first = client.post("/sms/ingest", json={"text": dated}, headers=AUTH).json()
check("back-dated alert is booked", first["status"] == "ok", first)
check("...and dated from the alert, not from now",
      first["transaction"]["created_at"].startswith("2026-08-05"), first["transaction"]["created_at"])
again = client.post("/sms/ingest", json={"text": dated}, headers=AUTH).json()
check("a retry of a back-dated alert is still deduped", again["status"] == "duplicate", again)
# Remove it again: later checks assert exact transaction counts and totals,
# and a probe row left behind would fail them for the wrong reason.
client.delete(f"/transactions/{first['transaction']['id']}", headers=AUTH)

# The SAME payment arriving through a DIFFERENT channel must be caught. An
# SMS and an email describe one payment in completely different words, so
# comparing alert text sees nothing — two real payments were booked twice
# exactly this way. The bank's own reference is the only reliable identity,
# and it is honoured with no time window: same reference, same payment, ever.
sms_form = "Sent Rs.76.00\nFrom HDFC Bank A/C *9393\nTo Ajantha Bakers\nOn 19/08/26\nRef 659721545407"
email_form = ("Dear Customer, Greetings from HDFC Bank! Rs.76.00 is debited from your account "
              "ending 9393 towards VPA paytm.s1d7kom@pty (Ajantha Backers-PNS) on 19-08-26. "
              "UPI transaction reference no.: 659721545407.")
a = client.post("/sms/ingest", json={"text": sms_form}, headers=AUTH).json()
check("first channel books the payment", a["status"] == "ok", a)
check("bank reference captured", a["transaction"]["bank_ref"] == "659721545407", a["transaction"])
b = client.post("/sms/ingest", json={"text": email_form}, headers=AUTH).json()
check("same payment via a different channel is deduped", b["status"] == "duplicate", b)
client.delete(f"/transactions/{a['transaction']['id']}", headers=AUTH)

# Two genuinely different payments to the same shop, same day, same amount
# must BOTH survive — over-eager dedupe would silently lose real spending.
p1 = client.post("/sms/ingest", json={"text": "Rs.50.00 debited to VPA shop@ybl (TEA STALL) on 19-08-26. UPI transaction reference no.: 111111111111."}, headers=AUTH).json()
p2 = client.post("/sms/ingest", json={"text": "Rs.50.00 debited to VPA shop@ybl (TEA STALL) on 19-08-26. UPI transaction reference no.: 222222222222."}, headers=AUTH).json()
check("different references are kept as separate payments",
      p1["status"] == "ok" and p2["status"] == "ok", (p1["status"], p2["status"]))
for x in (p1, p2):
    client.delete(f"/transactions/{x['transaction']['id']}", headers=AUTH)

r = client.post("/sms/ingest", json={"text": "894213 is your OTP for a transaction of Rs 2000. Do not share."}, headers=AUTH).json()
check("OTP ignored", r["status"] == "ignored", r)

r = client.post("/sms/ingest", json={"text": "Congratulations! You are pre-approved for a loan of Rs 5,00,000. Apply now!"}, headers=AUTH).json()
check("promo ignored", r["status"] == "ignored", r)

# "MADHURA SWEETS" used to be the example of an unknown merchant here, and
# stopped being one when "sweets" was added to the offline rules — a sweet shop
# now classifies for free, which is the point of that change. This needs a name
# that genuinely says nothing about what was bought, which is the real shape of
# an unclassifiable merchant.
r = client.post("/sms/ingest", json={"text": "INR 1,250.50 spent on HDFC Card x1234 at KRS ENTERPRISES on 2026-08-15"}, headers=AUTH).json()
check("unknown merchant -> needs review", r["transaction"]["needs_review"] is True, r["transaction"])
check("merchant extracted", r["transaction"]["merchant"] == "KRS ENTERPRISES", r["transaction"])
check("comma amount parsed", r["transaction"]["amount"] == 1250.50, r["transaction"])
review_id = r["transaction"]["id"]

# The one it replaced now takes the free path instead of an API call.
_sweet = client.post("/sms/ingest", json={"text": "INR 260.00 spent on HDFC Card x1234 at MADHURA SWEETS on 2026-08-16"}, headers=AUTH).json()["transaction"]
check("a sweet shop classifies offline, with no API call",
      _sweet["category"] == "Food & Dining" and _sweet["needs_review"] is False, _sweet)
client.delete(f"/transactions/{_sweet['id']}", headers=AUTH)

r = client.post("/sms/ingest", json={"text": "Rs 45000 credited to A/c XX1234 by salary transfer from ACME CORP"}, headers=AUTH).json()
check("credit direction detected", r["transaction"]["direction"] == "credit", r["transaction"])
check("credit auto-filed as Income", r["transaction"]["category"] == "Income", r["transaction"])
check("credit skips the review queue", r["transaction"]["needs_review"] is False, r["transaction"])

# A credit naming its sender must be ASKED about, not assumed to be income.
# The most common credit of all is money moved in from your own other bank,
# and a real Rs 10,000 self-transfer sat counted as income because of this.
self_txfr = ("Dear Customer, Greetings from HDFC Bank! We're writing to inform you that Rs.10000.00 has been "
             "successfully credited to your HDFC Bank account ending in 9393. Transaction Details: "
             "a. Date: 01-08-26 b. Sender: VISHNU ARAVIND R G (VPA: myown@oksbi) "
             "c. UPI Reference No.: 621312141199")
r = client.post("/sms/ingest", json={"text": self_txfr}, headers=AUTH).json()
t = r["transaction"]
check("credit alert names its sender, not 'Unknown'", t["merchant"] == "VISHNU ARAVIND R G", t)
check("sender's VPA becomes the identity key", t["payee_key"] == "myown@oksbi", t)
check("a credit from a new sender is asked about, not assumed income", t["needs_review"] is True, t)
self_id = t["id"]

# Answering "my account" must reclassify it as a transfer, so it stops
# counting as income, and must be remembered for next time.
r = client.patch(f"/transactions/{self_id}/classify",
                 json={"kind": "self", "label": "My SBI account"}, headers=AUTH).json()
check("answering 'my account' makes it a transfer", r["kind"] == "transfer", r)
income_before = client.get("/budget/summary", headers=AUTH).json()["total_income"]
again = client.post("/sms/ingest", json={"text": self_txfr.replace("621312141199", "621312141200")}, headers=AUTH).json()
check("the next transfer from that account is not asked again", again["transaction"]["needs_review"] is False, again)
check("...and is a transfer, not income", again["transaction"]["kind"] == "transfer", again)
income_after = client.get("/budget/summary", headers=AUTH).json()["total_income"]
check("a self-transfer never inflates income", abs(income_after - income_before) < 0.01,
      (income_before, income_after))
client.delete(f"/transactions/{self_id}", headers=AUTH)
client.delete(f"/transactions/{again['transaction']['id']}", headers=AUTH)

r = client.post("/sms/ingest", json={"text": "Rs 320 debited for UBER INDIA on 15-08-26"}, headers=AUTH).json()
check("uber -> Transport", r["transaction"]["category"] == "Transport", r["transaction"])
check("'debited for X' merchant parsed", r["transaction"]["merchant"] == "UBER INDIA", r["transaction"])

# The amount must never be mistaken for the merchant name.
p = parse_sms("Your a/c XX123 is debited for Rs 1000 towards NETFLIX on 03-08-26")
check("currency token not read as merchant", p["merchant"] == "NETFLIX", p)
p = parse_sms("Rs.499.00 debited from A/c XX1234 to VPA swiggy@icici (UPI Ref 402913)")
check("VPA handle preferred over boilerplate", p["merchant"] == "swiggy@icici", p)

# A parenthesised payee name beats the VPA — it's the only human-readable
# thing in the message, and it's what the AI categoriser gets handed. Storing
# the VPA instead ("q743985996@ybl") is why almost everything used to land in
# review: nothing can categorise an opaque handle.
p = parse_sms("Rs.280.00 is debited towards VPA q743985996@ybl (Ss Hyderabad Biriyani Peravallur) on 09-08-26.")
check("payee name preferred over the VPA", p["merchant"] == "Ss Hyderabad Biriyani Peravallur", p)
check("VPA still captured as the identity key", p["upi_id"] == "q743985996@ybl", p)
# ...but a reference number in those same parentheses is not a name.
p = parse_sms("Rs.499.00 debited to VPA swiggy@icici (UPI Ref 402913)")
check("reference number not mistaken for a payee name", p["merchant"] == "swiggy@icici", p)
# A branch number inside a real name must not trip the same guard.
p = parse_sms("Rs.194.00 is debited towards VPA paytm-82809956@ptys (FRESH SUPERMARKET PERAMBUR C1) on 05-08-26.")
check("name with a branch number survives", p["merchant"] == "FRESH SUPERMARKET PERAMBUR C1", p)
p = parse_sms("INR 1,250.50 spent on HDFC Card x1234 at MADHURA SWEETS on 2026-08-15")
check("'at X on' merchant parsed", p["merchant"] == "MADHURA SWEETS", p)

# Real HDFC debit-card email, footer and all. Two traps in one message:
#  - "from your HDFC Bank Debit Card" matches the merchant pattern first but
#    cleans to a stopword, and abandoning the pattern there missed the real
#    "at FLIPKART PAYMENTS on" later in the same text.
#  - the footer "We are here to support you in every step of the way" was
#    captured by an unanchored "to <name>" rule, and this exact alert was
#    genuinely booked in production with the merchant
#    "support you in every step of t".
_card_email = (
    "Dear Customer, Greetings from HDFC Bank! Rs.127.00 is debited from your "
    "HDFC Bank Debit Card ending 5564 at FLIPKART PAYMENTS on 19 Aug, 2026 at "
    "18:34:03. For more details on this transaction, please log in to NetBanking "
    "-> Accounts. If you did not authorize this transaction, please report it "
    "immediately at: 1. When in India (Toll free): 1800 258 6161. We are here to "
    "support you in every step of the way. Warm regards, HDFC Bank"
)
p = parse_sms(_card_email)
check("debit-card email finds the real merchant, not footer prose",
      p["merchant"] == "FLIPKART PAYMENTS", p)
check("debit-card email dated from 'on 19 Aug, 2026'",
      parse_alert_date(_card_email) == (2026, 8, 19), parse_alert_date(_card_email))

# The line-anchored "To <name>" rule must still win over an earlier "From <bank>".
p = parse_sms("Sent Rs.286.67\nFrom HDFC Bank A/C *9393\nTo VARSHA R S\nOn 19/08/26\nRef 659798226430")
check("'To <name>' still beats the sender's own bank", p["merchant"] == "VARSHA R S", p)

# ...and footer prose must not beat a real parenthesised payee name either.
p = parse_sms(
    "Rs.70.00 is debited from your account ending 9393 towards VPA "
    "paytm.s1fsral@pty (INIYA MUGIL SOUP) on 20-08-26. We are here to support "
    "you in every step of the way."
)
check("footer prose never beats the payee name", p["merchant"] == "INIYA MUGIL SOUP", p)

# --- manual entry -------------------------------------------------------------
r = client.post("/transactions/manual", json={"amount": 2000, "direction": "debit", "category": "Rent", "merchant": "Landlord"}, headers=AUTH)
check("manual expense created", r.status_code == 200, r.text)
manual_id = r.json()["id"]

bad = client.post("/transactions/manual", json={"amount": -5, "direction": "debit", "category": "Rent"}, headers=AUTH)
# Backdating: logging something after the fact must land on the day it
# happened, not today, or it lands in the wrong day (and possibly month).
r = client.post("/transactions/manual",
                json={"amount": 60, "direction": "debit", "category": "Groceries",
                      "merchant": "Backdated Shop", "occurred_on": "2026-08-05"},
                headers=AUTH).json()
check("manual entry honours occurred_on", r["created_at"].startswith("2026-08-05"), r["created_at"])
client.delete(f"/transactions/{r['id']}", headers=AUTH)
future = client.post("/transactions/manual",
                     json={"amount": 5, "direction": "debit", "category": "Rent",
                           "merchant": "Later", "occurred_on": "2099-01-01"}, headers=AUTH)
check("a future date is refused", future.status_code == 422, future.text)
bad_fmt = client.post("/transactions/manual",
                      json={"amount": 5, "direction": "debit", "category": "Rent",
                            "merchant": "X", "occurred_on": "05-08-2026"}, headers=AUTH)
check("a malformed date is refused", bad_fmt.status_code == 422, bad_fmt.text)

check("negative amount rejected", bad.status_code == 422, bad.text)
# A fat-fingered extra zero would silently wreck every total it touches.
absurd = client.post("/transactions/manual",
                     json={"amount": 1e15, "direction": "debit", "category": "Rent", "merchant": "Typo"},
                     headers=AUTH)
check("an absurd amount is refused", absurd.status_code == 422, absurd.text)
big_ok = client.post("/transactions/manual",
                     json={"amount": 250000, "direction": "debit", "category": "Rent", "merchant": "Big But Real"},
                     headers=AUTH)
check("a large but plausible amount is still allowed", big_ok.status_code == 200, big_ok.text)
if big_ok.status_code == 200:
    client.delete(f"/transactions/{big_ok.json()['id']}", headers=AUTH)
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

# --- the memory is visible, and reversible ------------------------------------
payees = client.get("/payees", headers=AUTH).json()
check("remembered answers are listable", any(p["label"] == "Arjun" for p in payees), payees)
arjun_key = next(p["key"] for p in payees if p["label"] == "Arjun")
check("payee keyed by the VPA, not the display name", arjun_key == "arjun123@ybl", arjun_key)

# A mis-tap in Review must be undoable, or it silently mis-files that payee forever.
check("forgetting a payee succeeds", client.delete(f"/payees/{arjun_key}", headers=AUTH).status_code == 200)
check("forgotten payee is gone", all(p["key"] != arjun_key for p in client.get("/payees", headers=AUTH).json()))
check("forgetting an unknown payee -> 404", client.delete("/payees/nope@nope", headers=AUTH).status_code == 404)
# Forgetting must NOT rewrite history — those totals are already counted.
still = client.get("/transactions", headers=AUTH).json()
check("existing transactions survive forgetting the payee",
      any(t["counterparty"] == "Arjun" for t in still), "lend row intact")
# Re-remember so the lending assertions further down still have their payee.
client.patch(f"/transactions/{p2p_id}/classify",
             json={"kind": "friend", "label": "Arjun", "remember": True}, headers=AUTH)

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

# --- mileage from a trip meter (no odometer at all) ------------------------------
# Most people reset the trip meter at the pump rather than copying a 6-digit
# odometer, so trip_km alone has to be enough. Reading X km at a fill means the
# previous tankful covered X km, hence km/L = trip_km / litres of THIS fill.
client.post("/vehicles", json={"id": "tripbike", "name": "Trip Bike", "type": "motorcycle"}, headers=AUTH)

# ONE fill is enough with a trip meter — the reading already encodes the
# distance the previous tankful covered, so there is nothing to pair with.
# Requiring a second fill here made a complete entry report no mileage.
client.post(
    "/fuel/fills",
    json={"vehicle_id": "tripbike", "amount": 925.40, "liters": 7.94, "trip_km": 186.9, "is_full_tank": True},
    headers=AUTH,
)
solo = client.get("/fuel/mileage?vehicle_id=tripbike", headers=AUTH).json()
check("a single trip-meter fill yields mileage: 186.9km/7.94L = 23.54 km/L",
      solo["avg_mileage"] is not None and abs(solo["avg_mileage"] - 23.54) < 0.02, solo)

client.post(
    "/fuel/fills",
    json={"vehicle_id": "tripbike", "amount": 400, "liters": 4, "trip_km": 180, "is_full_tank": True},
    headers=AUTH,
)
trip_m = client.get("/fuel/mileage?vehicle_id=tripbike", headers=AUTH).json()
check("second trip fill computes independently: 180km/4L = 45 km/L",
      any(abs(leg["km_per_liter"] - 45.0) < 0.01 for leg in trip_m["legs"]), trip_m)
check("trip legs don't need pairing, so both count", len(trip_m["legs"]) == 2, trip_m)

# A partial fill still must not become a leg, trip meter or not: its litres
# don't correspond to a full tank's worth of driving, so any km/L from it
# would be fiction. It must not add a leg, nor disturb the two real ones.
client.post(
    "/fuel/fills",
    json={"vehicle_id": "tripbike", "amount": 100, "liters": 1, "trip_km": 50, "is_full_tank": False},
    headers=AUTH,
)
trip_m2 = client.get("/fuel/mileage?vehicle_id=tripbike", headers=AUTH).json()
check("partial fill adds no leg, trip meter or not", len(trip_m2["legs"]) == 2, trip_m2)
check("partial fill's 50km/1L=50 never appears as a leg",
      all(abs(leg["km_per_liter"] - 50.0) > 0.01 for leg in trip_m2["legs"]), trip_m2)
# ...but its litres and cost are still real money and real fuel.
check("partial fill still counts toward spend and litres",
      abs(trip_m2["total_liters"] - 12.94) < 0.01, trip_m2)

# A third full fill averages in with the rest, each leg standing alone.
client.post(
    "/fuel/fills",
    json={"vehicle_id": "tripbike", "amount": 400, "liters": 4, "trip_km": 200, "is_full_tank": True},
    headers=AUTH,
)
trip_m3 = client.get("/fuel/mileage?vehicle_id=tripbike", headers=AUTH).json()
check("third trip leg computes independently: 200km/4L = 50 km/L",
      any(abs(leg["km_per_liter"] - 50.0) < 0.01 for leg in trip_m3["legs"]), trip_m3)
check("all three trip legs stand alone", len(trip_m3["legs"]) == 3, trip_m3)
check("avg across the three legs: (23.54+45+50)/3 = 39.51",
      abs(trip_m3["avg_mileage"] - 39.51) < 0.02, trip_m3)
check("last_mileage is the most recent leg, not the average",
      abs(trip_m3["last_mileage"] - 50.0) < 0.01, trip_m3)

# A payee remembered as "paying back what I owe" (friend_settle) must keep
# that meaning on their NEXT payment. friend_settle was added to
# _resolve_kind but not to _ingest's remembered-payee chain, so it matched no
# branch and fell through to a plain expense — silently counting a debt
# settlement as spending. Same class of bug as the earlier "merchant" vs
# "expense" vocabulary mismatch.
settle_sms = "Rs.900.00 debited from a/c XXXX1234 on 18-08-26 to VPA owedfriend@ybl Ref 730000001"
r = client.post("/sms/ingest", json={"text": settle_sms}, headers=AUTH).json()
settle_id = r["transaction"]["id"]
client.patch(f"/transactions/{settle_id}/classify",
             json={"kind": "friend_settle", "label": "Owed Friend", "remember": True}, headers=AUTH)

spend_before = client.get("/budget/summary", headers=AUTH).json()["total_spent"]
again = client.post("/sms/ingest",
                    json={"text": "Rs.400.00 debited from a/c XXXX1234 on 19-08-26 to VPA owedfriend@ybl Ref 730000002"},
                    headers=AUTH).json()["transaction"]
check("a remembered debt-settlement payee stays a transfer", again["kind"] == "transfer", again)
check("...and is not filed as Uncategorized", again["category"] == "Transfer", again)
check("...and is not asked about again", again["needs_review"] is False, again)
spend_after = client.get("/budget/summary", headers=AUTH).json()["total_spent"]
check("settling a debt never counts as spending", abs(spend_after - spend_before) < 0.01,
      (spend_before, spend_after))
for tid in (settle_id, again["id"]):
    client.delete(f"/transactions/{tid}", headers=AUTH)

# --- the database itself must reject a duplicate reference --------------------
# An application-level "check then insert" cannot hold under concurrency, and
# didn't: six simultaneous copies of one alert all passed the check before any
# committed, and Rs 333 was booked six times. The guarantee has to live in the
# schema, so assert the constraint is really there rather than trusting that
# the sequential path happens to behave.
from sqlalchemy.exc import IntegrityError as _IntegrityError
from db import SessionLocal as _SessionLocal
from models import Transaction as _Txn

_s = _SessionLocal()
try:
    _s.add(_Txn(merchant="Ref A", amount=10, direction="debit", category="Other",
                source="manual", kind="expense", bank_ref="dup-ref-check"))
    _s.commit()
    _s.add(_Txn(merchant="Ref B", amount=20, direction="debit", category="Other",
                source="manual", kind="expense", bank_ref="dup-ref-check"))
    try:
        _s.commit()
        check("the database rejects a second row with the same bank_ref", False,
              "the insert succeeded - the unique index is missing")
    except _IntegrityError:
        _s.rollback()
        check("the database rejects a second row with the same bank_ref", True)

    # NULL must stay exempt, or every alert without a reference would collide.
    _s.add(_Txn(merchant="No Ref A", amount=1, direction="debit", category="Other",
                source="manual", kind="expense", bank_ref=None))
    _s.add(_Txn(merchant="No Ref B", amount=1, direction="debit", category="Other",
                source="manual", kind="expense", bank_ref=None))
    try:
        _s.commit()
        check("rows without a reference are still allowed alongside each other", True)
    except _IntegrityError:
        _s.rollback()
        check("rows without a reference are still allowed alongside each other", False,
              "NULL is being treated as a duplicate")
finally:
    for _m in ("Ref A", "Ref B", "No Ref A", "No Ref B"):
        for _r in _s.query(_Txn).filter(_Txn.merchant == _m).all():
            _s.delete(_r)
    _s.commit()
    _s.close()

# And the ingest path must report it as a duplicate rather than a 500.
_ref_sms = "Rs.55.00 debited from a/c XXXX1234 on 20-08-26 to VPA refrace@ybl Ref 880000123456"
_a = client.post("/sms/ingest", json={"text": _ref_sms}, headers=AUTH).json()
_b = client.post("/sms/ingest", json={"text": _ref_sms + " (email copy)"}, headers=AUTH).json()
check("a second copy carrying the same reference is reported as duplicate",
      _b["status"] == "duplicate", _b)
client.delete(f"/transactions/{_a['transaction']['id']}", headers=AUTH)

# --- screenshot duplicate detection -------------------------------------------
# A screenshot re-uploaded, or a vision model reading one line twice, must not
# be able to double-count. Equally, over-eager matching would silently DROP
# real spending, so the "must not flag" cases matter just as much.
from main import _flag_duplicates as _flag
from db import SessionLocal as _SL
from models import Transaction as _T
from timeutil import local_date_to_utc as _ldu

_d = _SL()
_seed = [
    _T(merchant="Swiggy", amount=76, direction="debit", category="Food & Dining",
       source="manual", kind="expense", created_at=_ldu(2026, 8, 22)),
    _T(merchant="Amazon Pay Gift Card", amount=232, direction="debit", category="Shopping",
       source="manual", kind="expense", created_at=_ldu(2026, 8, 21)),
    _T(merchant="Self transfer", amount=10000, direction="credit", category="Transfer",
       source="manual", kind="transfer", created_at=_ldu(2026, 8, 21)),
]
for _r in _seed: _d.add(_r)
_d.commit()

_rows = [
    {"merchant": "Swiggy", "amount": 76.0, "occurred_on": "2026-08-22", "direction": "debit", "category": "Food & Dining"},
    {"merchant": "Amazon Pay Gift Card", "amount": 232.0, "occurred_on": "2026-08-21", "direction": "debit", "category": "Shopping"},
    {"merchant": "Amazon Pay Gift Card", "amount": 232.0, "occurred_on": "2026-08-21", "direction": "debit", "category": "Shopping"},
    {"merchant": "Self transfer", "amount": 10000.0, "occurred_on": "2026-08-21", "direction": "debit", "category": "Transfer"},
    {"merchant": "METROPOLITAN TRANSPORT CORPORATION", "amount": 19.0, "occurred_on": "2026-08-22", "direction": "debit", "category": "Transport"},
    {"merchant": "Swiggy", "amount": 512.0, "occurred_on": "2026-08-22", "direction": "debit", "category": "Food & Dining"},
]
_flag(_rows, _d)
check("an already-recorded row is flagged", _rows[0]["already_recorded"] is True, _rows[0])
check("the second identical row in one screenshot is flagged",
      _rows[2]["already_recorded"] and "twice in this screenshot" in (_rows[2]["duplicate_reason"] or ""),
      _rows[2])
check("a CREDIT already stored is flagged too (was debit-only before)",
      _rows[3]["already_recorded"] is True, _rows[3])
check("a genuinely new row is NOT flagged", _rows[4]["already_recorded"] is False, _rows[4])
check("same merchant, different amount is NOT flagged", _rows[5]["already_recorded"] is False, _rows[5])
check("a reason is given for every flagged row",
      all(r["duplicate_reason"] for r in _rows if r["already_recorded"]), _rows)

# Sources disagree about dates near midnight, so a day either side counts —
# but only when the merchant name shares a distinctive word.
_near = [
    {"merchant": "Swiggy Limited", "amount": 76.0, "occurred_on": "2026-08-23", "direction": "debit", "category": "Food & Dining"},
    {"merchant": "Totally Different Shop", "amount": 76.0, "occurred_on": "2026-08-23", "direction": "debit", "category": "Other"},
]
_flag(_near, _d)
check("same amount a day apart WITH a matching name is flagged", _near[0]["already_recorded"] is True, _near[0])
check("same amount a day apart with an unrelated name is NOT flagged",
      _near[1]["already_recorded"] is False, _near[1])

# A row with no readable date can't be matched on date; it must not be
# flagged purely because some old row happens to share the amount.
_nodate = [{"merchant": "Mystery", "amount": 76.0, "occurred_on": None, "direction": "debit", "category": "Other"}]
_flag(_nodate, _d)
check("a row with no date is left for the user to judge", _nodate[0]["already_recorded"] is False, _nodate[0])

for _r in _seed: _d.delete(_r)
_d.commit(); _d.close()

# --- timezone boundaries -------------------------------------------------------
# Timestamps are stored as naive UTC but months and days are cut in local
# time (IST, +5:30). A spend just before local midnight is over five hours
# EARLIER in UTC, so a window computed in UTC would pull it into the previous
# day — and on the 1st, the previous month's budget.
from datetime import datetime, timedelta, timezone as _tz
from timeutil import LOCAL_TZ, local_day_key, month_range_utc, period_range_utc

def as_stored(y, mo, d, hh, mm):
    """A local wall-clock moment, as it would be stored (naive UTC)."""
    return datetime(y, mo, d, hh, mm, tzinfo=LOCAL_TZ).astimezone(_tz.utc).replace(tzinfo=None)

late_aug = as_stored(2026, 8, 31, 23, 59)      # last minute of August, locally
early_sep = as_stored(2026, 9, 1, 0, 1)        # first minute of September
aug_s, aug_e = month_range_utc(2026, 8)
sep_s, sep_e = month_range_utc(2026, 9)
check("23:59 on 31 Aug local falls inside August", aug_s <= late_aug < aug_e, (late_aug, aug_s, aug_e))
check("...and NOT inside September", not (sep_s <= late_aug < sep_e), late_aug)
check("00:01 on 1 Sep local falls inside September", sep_s <= early_sep < sep_e, (early_sep, sep_s, sep_e))
check("...and NOT inside August", not (aug_s <= early_sep < aug_e), early_sep)
check("a near-midnight spend keeps its LOCAL day", local_day_key(late_aug) == "2026-08-31", local_day_key(late_aug))
check("a just-past-midnight spend keeps its LOCAL day", local_day_key(early_sep) == "2026-09-01", local_day_key(early_sep))

# Month windows must tile without gaps or overlaps, or spend falls between them.
check("August ends exactly where September begins", aug_e == sep_s, (aug_e, sep_s))
jul_s, jul_e = month_range_utc(2026, 7)
check("July ends exactly where August begins", jul_e == aug_s, (jul_e, aug_s))

# Day windows must be exactly 24h apart and tile too.
d0s, d0e = period_range_utc("day", 0)
d1s, d1e = period_range_utc("day", 1)
check("a day window is exactly 24 hours", d0e - d0s == timedelta(days=1), d0e - d0s)
check("yesterday ends where today begins", d1e == d0s, (d1e, d0s))
w0s, w0e = period_range_utc("week", 0)
check("a week window is exactly 7 days", w0e - w0s == timedelta(days=7), w0e - w0s)

# --- day / week / month review --------------------------------------------------
# Every figure must filter on kind == "expense". A review screen that counted
# lending or wallet top-ups would misreport the number looked at most.
for p_ in ("day", "week", "month"):
    r = client.get(f"/stats/summary?period={p_}", headers=AUTH)
    check(f"{p_} summary responds", r.status_code == 200, r.text)
    body = r.json()
    check(f"{p_} summary has a label", bool(body["label"]), body)
    check(f"{p_} totals are never negative", body["total_spent"] >= 0, body)

check("an unknown period is rejected",
      client.get("/stats/summary?period=fortnight", headers=AUTH).status_code == 422)

# A month window must agree with the month view that already exists — two
# different code paths reporting different totals for the same month would be
# worse than having no review screen at all.
month_sum = client.get("/stats/summary?period=month", headers=AUTH).json()
budget_sum = client.get("/budget/summary", headers=AUTH).json()
check("month review agrees with the budget summary",
      abs(month_sum["total_spent"] - budget_sum["total_spent"]) < 0.01,
      (month_sum["total_spent"], budget_sum["total_spent"]))

# Lending must not leak into the review totals.
before_rev = client.get("/stats/summary?period=month", headers=AUTH).json()["total_spent"]
lend = client.post("/transactions/manual",
                   json={"amount": 3000, "direction": "debit", "kind": "friend",
                         "merchant": "Review Leak Test"}, headers=AUTH).json()
after_rev = client.get("/stats/summary?period=month", headers=AUTH).json()["total_spent"]
check("lending does not move the review total", abs(after_rev - before_rev) < 0.01,
      (before_rev, after_rev))
client.delete(f"/transactions/{lend['id']}", headers=AUTH)

# Period windows must not overlap — an overlap would double-count a spend in
# both "this week" and "last week".
from timeutil import period_range_utc
for p_ in ("day", "week", "month"):
    cur_s, cur_e = period_range_utc(p_, 0)
    prv_s, prv_e = period_range_utc(p_, 1)
    check(f"{p_}: previous window ends exactly where current begins", prv_e == cur_s, (prv_e, cur_s))
    check(f"{p_}: current window is non-empty", cur_e > cur_s, (cur_s, cur_e))

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
# --- repaid in cash --------------------------------------------------------------
# A cash repayment generates no bank alert, so without an explicit action the
# debt sits on the Lending card forever and the reminder nags about money
# that's already back.
client.post("/sms/ingest", json={"text": "Rs.900.00 debited from a/c XXXX1234 on 18-08-26 to VPA cashfriend@ybl Ref 700000001"}, headers=AUTH)
cash_id = client.get("/transactions/needs-review", headers=AUTH).json()[0]["id"]
client.patch(f"/transactions/{cash_id}/classify", json={"kind": "friend", "label": "Cash Friend"}, headers=AUTH)
bal = {b["person"]: b for b in client.get("/lending", headers=AUTH).json()}
check("cash friend owes the full amount", bal["Cash Friend"]["outstanding"] == 900.0, bal.get("Cash Friend"))

spent_pre = client.get("/budget/summary", headers=AUTH).json()["total_spent"]
r = client.post("/lending/Cash Friend/repaid?amount=400", headers=AUTH).json()
check("partial cash repayment books a repayment row", r["kind"] == "repayment" and r["amount"] == 400.0, r)
bal = {b["person"]: b for b in client.get("/lending", headers=AUTH).json()}
check("outstanding drops by the partial amount", bal["Cash Friend"]["outstanding"] == 500.0, bal.get("Cash Friend"))
spent_post = client.get("/budget/summary", headers=AUTH).json()["total_spent"]
check("a repayment is not spending and moves no total", spent_pre == spent_post, (spent_pre, spent_post))

client.post("/lending/Cash Friend/repaid", headers=AUTH)
bal = {b["person"]: b for b in client.get("/lending", headers=AUTH).json()}
check("settling the rest clears the debt", bal["Cash Friend"]["outstanding"] == 0.0, bal.get("Cash Friend"))
check("over-repaying is refused, not silently accepted",
      client.post("/lending/Cash Friend/repaid?amount=50", headers=AUTH).status_code == 409)
check("repaid on an unknown person -> 404",
      client.post("/lending/Nobody At All/repaid", headers=AUTH).status_code == 404)

# --- the date a transaction happened, not the date it was ingested ---------------
from categorizer import parse_alert_date
check("HDFC 'on 19-08-26' parsed", parse_alert_date("debited ... on 19-08-26. UPI ref") == (2026, 8, 19))
check("slash form 'on 19/08/26' parsed", parse_alert_date("To VARSHA\nOn 19/08/26\nRef 1") == (2026, 8, 19))
check("card format 'on 09 Aug, 2026' parsed", parse_alert_date("at FLIPKART on 09 Aug, 2026 at 13:18:43") == (2026, 8, 9))
check("ISO 'on 2026-08-15' parsed", parse_alert_date("at MADHURA SWEETS on 2026-08-15") == (2026, 8, 15))
check("'Date: 18-08-26' parsed", parse_alert_date("Transaction Details: a. Date: 18-08-26 b.") == (2026, 8, 18))
# A 12-digit UPI reference is a rich source of accidental dates — it must not
# be mistaken for one, or every transaction gets a fabricated date.
check("bare reference number is not read as a date", parse_alert_date("UPI transaction reference no.: 659798226430") is None)
check("impossible date rejected", parse_alert_date("on 31-02-26") is None)

check("reminder can be cleared",clear_reminder.status_code == 200, clear_reminder.text)

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

# --- merchant rename (location-resolved place name after the fact) -----------
loc_txn = client.post(
    "/transactions/manual",
    json={"amount": 220, "direction": "debit", "category": "Food & Dining"},
    headers=AUTH,
).json()
check("transaction added with no merchant", loc_txn["merchant"] is None, loc_txn)

renamed = client.patch(
    f"/transactions/{loc_txn['id']}/merchant", json={"merchant": "St. Joseph's Indian High School"}, headers=AUTH
).json()
check("merchant renamed", renamed["merchant"] == "St. Joseph's Indian High School", renamed)
check("category/kind untouched by a merchant rename", renamed["category"] == "Food & Dining" and renamed["kind"] == "expense", renamed)

check("renaming a missing transaction -> 404", client.patch("/transactions/999999/merchant", json={"merchant": "x"}, headers=AUTH).status_code == 404)
check("empty merchant name rejected", client.patch(f"/transactions/{loc_txn['id']}/merchant", json={"merchant": ""}, headers=AUTH).status_code == 422)

# --- manually logging money sent to a friend (not via SMS) --------------------
before_summary = client.get("/budget/summary", headers=AUTH).json()
spent_before_manual_lend = before_summary["total_spent"]

lend_manual = client.post(
    "/transactions/manual",
    json={"amount": 1500, "direction": "debit", "kind": "friend", "merchant": "Rahul"},
    headers=AUTH,
).json()
check("manually-logged loan gets kind=lend, not expense", lend_manual["kind"] == "lend", lend_manual)
check("counterparty recorded from the merchant field", lend_manual["counterparty"] == "Rahul", lend_manual)
check("category defaults to Lending, not whatever category was left selected", lend_manual["category"] == "Lending", lend_manual)

after_summary = client.get("/budget/summary", headers=AUTH).json()
check(
    "manual lending does NOT move total_spent",
    after_summary["total_spent"] == spent_before_manual_lend,
    {"before": spent_before_manual_lend, "after": after_summary["total_spent"]},
)

rahul_balance = next((p for p in client.get("/lending", headers=AUTH).json() if p["person"] == "Rahul"), None)
check("Rahul shows up in /lending from a manual entry alone", rahul_balance is not None and rahul_balance["lent"] == 1500.0, rahul_balance)

# A manually-logged wallet top-up should behave the same way.
topup_manual = client.post(
    "/transactions/manual", json={"amount": 300, "direction": "debit", "kind": "wallet", "merchant": "Paytm"}, headers=AUTH
).json()
check("manually-logged wallet top-up gets kind=topup", topup_manual["kind"] == "topup", topup_manual)
check("wallet top-up category defaults to Transfer", topup_manual["category"] == "Transfer", topup_manual)

# Ordinary manual expense still behaves exactly as before this change.
plain_manual = client.post(
    "/transactions/manual", json={"amount": 99, "direction": "debit", "category": "Shopping"}, headers=AUTH
).json()
check("plain manual expense unaffected by the kind field's addition", plain_manual["kind"] == "expense" and plain_manual["category"] == "Shopping", plain_manual)

check("invalid manual kind rejected", client.post("/transactions/manual", json={"amount": 10, "direction": "debit", "kind": "bogus"}, headers=AUTH).status_code == 422)

# --- receipt photo scanning (no GEMINI_API_KEY set in this test env, deliberately —
# same reasoning as skipping ANTHROPIC_API_KEY above: free, deterministic, offline) --
status = client.get("/ai/status", headers=AUTH).json()
check("ai/status reports unavailable with no key set", status["receipt_scan_available"] is False, status)

fake_image = client.post(
    "/ai/scan-receipt",
    files={"image": ("receipt.jpg", b"\xff\xd8\xff\xe0not-a-real-jpeg", "image/jpeg")},
    headers=AUTH,
)
check("scan-receipt refuses cleanly with no key, not a 500", fake_image.status_code == 503, fake_image.text)

empty_image = client.post("/ai/scan-receipt", files={"image": ("x.jpg", b"", "image/jpeg")}, headers=AUTH)
check("scan-receipt still checks key before validating the file", empty_image.status_code == 503, empty_image.text)

check("scan-receipt needs auth", client.post("/ai/scan-receipt", files={"image": ("x.jpg", b"123", "image/jpeg")}).status_code == 401)

# --- a free-text note, for when the category explains nothing --------------------
# "Other" carries no meaning, so the user's own words are the only record of
# what a payment actually was. Kept separate from `merchant` so it can never
# corrupt the remembered payee label.
client.post("/sms/ingest", json={"text": "Rs.500.00 debited from a/c XXXX1234 on 18-08-26 to VPA notetest@ybl Ref 700000009"}, headers=AUTH)
note_id = client.get("/transactions/needs-review", headers=AUTH).json()[0]["id"]
r = client.patch(
    f"/transactions/{note_id}/classify",
    json={"kind": "expense", "category": "Other", "label": "HARI HARAN AGENCIES 3", "note": "hardware for the bike"},
    headers=AUTH,
).json()
check("note stored alongside the category", r["note"] == "hardware for the bike", r)
check("note does not overwrite the merchant name", r["merchant"] == "HARI HARAN AGENCIES 3", r)
remembered = {p["key"]: p for p in client.get("/payees", headers=AUTH).json()}
check("remembered label is the merchant, never the note",
      remembered["notetest@ybl"]["label"] == "HARI HARAN AGENCIES 3", remembered.get("notetest@ybl"))


# --- credit alerts that name a sender, and ones that don't -----------------------
# Three real credits totalling Rs 11,912 - one of them Rs 10,000 - were stored
# as merchant "Unknown" because HDFC's email template puts the sender in a
# lettered field with no VPA beside it, and the pattern required the VPA.
named_credit = client.post("/sms/ingest", json={"text":
    "Dear Customer, Greetings from HDFC Bank! We're writing to inform you that Rs.1764.00 has been "
    "successfully credited to your HDFC Bank account ending in 9393. Transaction Details: "
    "a. Date: 01-08-26 b. Sender: SG JEYASHRI c. Amount: Rs.1764.00 Ref 810000021"
}, headers=AUTH).json()
check("a sender named without a VPA is read", named_credit["transaction"]["merchant"] == "SG JEYASHRI", named_credit["transaction"])
check("...and still filed as income", named_credit["transaction"]["kind"] == "income", named_credit["transaction"])

# The same template truncated before the sender field - which is how those rows
# actually arrived. Nothing can name the sender here, so the test is that the
# app says so honestly instead of inventing a name or shrugging.
anon = client.post("/sms/ingest", json={"text":
    "Dear Customer, Greetings from HDFC Bank! We're writing to inform you that Rs.10000.00 has been "
    "successfully credited to your HDFC Bank account ending in 9393. Transaction Details: "
    "a. Date: 02-08-26 b. Ref 810000022"
}, headers=AUTH).json()["transaction"]
check("an unnamed credit says which account it landed in, not 'Unknown'",
      anon["merchant"] == "Credit to HDFC ...9393", anon)
check("an unnamed credit is queued for review rather than filed silently",
      anon["needs_review"] is True, anon)
# The placeholder must never become an identity: keyed off, it would fold every
# anonymous credit from anyone into one remembered "payee", never asked about.
check("an unnamed credit is NOT remembered as a payee", anon["payee_key"] is None, anon)

remembered_keys = {p["key"] for p in client.get("/payees", headers=AUTH).json()}
check("...so no placeholder payee is created",
      not any("credit to hdfc" in k.lower() for k in remembered_keys), remembered_keys)

# A debit's "From <bank>" line must not be mistaken for a sender by the widened
# credit patterns - that would name every card swipe after the user's own bank.
debit_from = client.post("/sms/ingest", json={"text":
    "Sent Rs.76.00\nFrom HDFC Bank A/C *9393\nTo Swiggy\nOn 22-08-26\nRef 810000023"
}, headers=AUTH).json()["transaction"]
check("a debit's own 'From <bank>' line is not read as a counterparty",
      debit_from["merchant"] == "Swiggy", debit_from)

for _t in (named_credit["transaction"], anon, debit_from):
    client.delete(f"/transactions/{_t['id']}", headers=AUTH)

# --- capture health: has automatic capture gone quiet? ---------------------------
health = client.get("/stats/capture-health", headers=AUTH).json()
check("capture health reports a last-heard time", health["last_at"] is not None, health)
check("capture health is not quiet right after an alert", health["quiet"] is False, health)
check("capture health names the channel", health["last_source"] == "sms", health)
check("capture health lists per-channel detail", any(c["source"] == "sms" for c in health["channels"]), health)
# A channel that never delivered anything is not an outage - Gmail polling was
# connected long after SMS forwarding, and calling a pipe that was never
# plugged in "quiet" is noise, not a warning.
check("a channel that never delivered isn't reported as broken",
      all(c["source"] != "gmail" for c in health["channels"]), health)

# Backdate the arrival time of every automated row and the alarm must fire.
# ingested_at, not created_at: an email arriving today about last Tuesday must
# not read as five days of silence.
from datetime import timedelta as _td  # noqa: E402

from db import SessionLocal  # noqa: E402
from models import Transaction as _Txn  # noqa: E402
from timeutil import local_now as _local_now  # noqa: E402
from timeutil import utc_now_naive as _now  # noqa: E402

_session = SessionLocal()
_moved = []
for _row in _session.query(_Txn).filter(_Txn.source.in_(("sms", "gmail"))).all():
    _moved.append((_row.id, _row.ingested_at))
    _row.ingested_at = _now() - _td(hours=50)
_session.commit()
_session.close()

quiet = client.get("/stats/capture-health", headers=AUTH).json()
check("50 hours of silence trips the alarm", quiet["quiet"] is True, quiet)
check("...and says how long it has been", quiet["hours_since_last"] >= 49, quiet)
check("...and counts what was typed in by hand meanwhile", quiet["manual_since"] > 0, quiet)

_session = SessionLocal()
for _id, _when in _moved:
    _session.get(_Txn, _id).ingested_at = _when
_session.commit()
_session.close()
check("restoring the timestamps clears the alarm",
      client.get("/stats/capture-health", headers=AUTH).json()["quiet"] is False)

# --- correcting a remembered answer ----------------------------------------------
# The real case: RADDLINS FOOD, a bakery, answered as "a person". Its payments
# were filed as money lent out, it sat in the who-owes-you list beside actual
# friends, and a remembered payee is never asked about again - so nothing would
# ever have surfaced it.
client.post("/sms/ingest", json={"text":
    "Rs.67.00 debited from a/c XXXX1234 on 19-08-26 to VPA vyapar.175693560002@hdfcbank (RADDLINS FOOD) Ref 810000031"
}, headers=AUTH)
queued = client.get("/transactions/needs-review", headers=AUTH).json()[0]
check("the review queue warns when a payee looks like a business",
      queued["business_hint"] is not None and "Vyapar" in (queued["business_hint"] or ""), queued)

client.patch(f"/transactions/{queued['id']}/classify",
             json={"kind": "friend", "label": "RADDLINS FOOD"}, headers=AUTH)
lent = client.get("/lending", headers=AUTH).json()
check("answering 'a person' does put a shop in the lending list (the bug)",
      any(p["person"] == "RADDLINS FOOD" for p in lent), lent)

payees = {p["key"]: p for p in client.get("/payees", headers=AUTH).json()}
bakery = payees["vyapar.175693560002@hdfcbank"]
check("the Remembered list flags it as shop-shaped", bakery["business_hint"] is not None, bakery)
check("...and says how many transactions the answer decided", bakery["used_by"] >= 1, bakery)

# A genuine friend must NOT be flagged - a false warning here pushes people to
# mis-file real lending as spending, which is the more expensive mistake.
client.post("/sms/ingest", json={"text":
    "Rs.500.00 debited from a/c XXXX1234 on 19-08-26 to VPA scientificmonesh@okhdfcbank (Monesh Kumar R) Ref 810000032"
}, headers=AUTH)
friend_row = client.get("/transactions/needs-review", headers=AUTH).json()[0]
client.patch(f"/transactions/{friend_row['id']}/classify",
             json={"kind": "friend", "label": "Monesh Kumar R"}, headers=AUTH)
payees = {p["key"]: p for p in client.get("/payees", headers=AUTH).json()}
check("a real person is not flagged as a business",
      payees["scientificmonesh@okhdfcbank"]["business_hint"] is None,
      payees["scientificmonesh@okhdfcbank"])

spent_before_fix = client.get("/budget/summary", headers=AUTH).json()["total_spent"]
fix = client.patch("/payees/vyapar.175693560002@hdfcbank",
                   json={"kind": "expense", "category": "Food & Dining", "apply_to_past": True},
                   headers=AUTH).json()
check("correcting a payee re-files the transactions it already decided", fix["updated"] >= 1, fix)
check("the corrected answer is no longer flagged", fix["payee"]["business_hint"] is None, fix)
check("a corrected shop keeps its category as the remembered default",
      fix["payee"]["default_category"] == "Food & Dining", fix)
# used_by is computed rather than stored, so it has to be filled in on this
# response too — left out, the same fact had two answers depending on which
# call the screen happened to read.
check("the correction response agrees with the list about how much it decided",
      fix["payee"]["used_by"] ==
      next(p["used_by"] for p in client.get("/payees", headers=AUTH).json()
           if p["key"] == "vyapar.175693560002@hdfcbank"),
      fix["payee"])

lent_after = client.get("/lending", headers=AUTH).json()
check("the shop is gone from the lending list",
      not any(p["person"] == "RADDLINS FOOD" for p in lent_after), lent_after)
check("the real friend stays in the lending list",
      any(p["person"] == "Monesh Kumar R" for p in lent_after), lent_after)
spent_after_fix = client.get("/budget/summary", headers=AUTH).json()["total_spent"]
check("money wrongly counted as lending becomes spending again",
      round(spent_after_fix - spent_before_fix, 2) == 67.0,
      {"before": spent_before_fix, "after": spent_after_fix})

# Settling a debt that never existed leaves a marker behind. This is the exact
# shape found in production: a bakery answered as "a person", the resulting
# "loan" cleared with the cash-repaid button, and a repayment row left
# recording money returned for a debt that was never lent.
client.post("/sms/ingest", json={"text":
    "Rs.150.00 debited from a/c XXXX1234 on 15-08-26 to VPA vyapar.99887766@hdfcbank (CORNER TRADERS) Ref 810000041"
}, headers=AUTH)
_bakery2 = client.get("/transactions/needs-review", headers=AUTH).json()[0]
client.patch(f"/transactions/{_bakery2['id']}/classify",
             json={"kind": "friend", "label": "CORNER TRADERS"}, headers=AUTH)
client.post("/lending/CORNER TRADERS/repaid", headers=AUTH)
_settled = [t for t in client.get("/transactions", headers=AUTH).json()
            if t["counterparty"] == "CORNER TRADERS" and t["kind"] == "repayment"]
check("marking a fictional loan repaid writes a settlement row", len(_settled) == 1, _settled)

# A genuine repayment that arrived as a bank credit must survive the same
# correction untouched - it has alert text behind it and really happened.
client.post("/sms/ingest", json={"text":
    "Rs.400.00 credited to your a/c XX1234 on 16-08-26 b. Sender: REAL FRIEND (VPA: realfriend@ybl) Ref 810000042"
}, headers=AUTH)
_credit = client.get("/transactions/needs-review", headers=AUTH).json()[0]
client.patch(f"/transactions/{_credit['id']}/classify",
             json={"kind": "friend", "label": "REAL FRIEND"}, headers=AUTH)

_fix2 = client.patch("/payees/vyapar.99887766@hdfcbank",
                     json={"kind": "expense", "category": "Food & Dining", "apply_to_past": True},
                     headers=AUTH).json()
check("correcting the answer clears the settlement for a debt that never existed",
      _fix2["removed"] == 1, _fix2)
_left = [t for t in client.get("/transactions", headers=AUTH).json()
         if t["counterparty"] == "CORNER TRADERS"]
check("...leaving no phantom repayment behind", _left == [], _left)
_real = [t for t in client.get("/transactions", headers=AUTH).json()
         if t["counterparty"] == "REAL FRIEND"]
check("a real repayment backed by a bank alert is untouched", len(_real) == 1, _real)
check("correcting a shop to another shop removes nothing",
      client.patch("/payees/vyapar.99887766@hdfcbank",
                   json={"kind": "expense", "category": "Groceries", "apply_to_past": True},
                   headers=AUTH).json()["removed"] == 0)
for _t in client.get("/transactions", headers=AUTH).json():
    if _t["merchant"] in ("CORNER TRADERS", "REAL FRIEND"):
        client.delete(f"/transactions/{_t['id']}", headers=AUTH)

# Correcting without apply_to_past must change nothing historical - moving old
# totals is a choice, not a side effect.
before_noapply = client.get("/budget/summary", headers=AUTH).json()["total_spent"]
client.patch("/payees/scientificmonesh@okhdfcbank",
             json={"kind": "friend", "label": "Monesh Kumar R"}, headers=AUTH)
check("correcting without apply_to_past leaves history alone",
      client.get("/budget/summary", headers=AUTH).json()["total_spent"] == before_noapply)
# Correcting to a shop without naming a category must not leave the row
# labelled "Lending" - the kind would be right and the label nonsense. It asks
# instead of guessing.
client.post("/sms/ingest", json={"text":
    "Rs.90.00 debited from a/c XXXX1234 on 19-08-26 to VPA nocategory@ybl (SOME SHOP) Ref 810000033"
}, headers=AUTH)
_row = client.get("/transactions/needs-review", headers=AUTH).json()[0]
client.patch(f"/transactions/{_row['id']}/classify", json={"kind": "friend", "label": "SOME SHOP"}, headers=AUTH)
client.patch("/payees/nocategory@ybl", json={"kind": "expense", "apply_to_past": True}, headers=AUTH)
_fixed = next(t for t in client.get("/transactions", headers=AUTH).json() if t["id"] == _row["id"])
check("re-filing to a shop with no category given drops the stale 'Lending' label",
      _fixed["category"] == "Uncategorized", _fixed)
check("...and asks for the real category instead of guessing", _fixed["needs_review"] is True, _fixed)
client.delete(f"/transactions/{_row['id']}", headers=AUTH)

check("correcting an unremembered payee -> 404",
      client.patch("/payees/nobody@nowhere", json={"kind": "expense"}, headers=AUTH).status_code == 404)
check("an invalid corrected kind is rejected",
      client.patch("/payees/scientificmonesh@okhdfcbank", json={"kind": "bogus"}, headers=AUTH).status_code == 422)

# --- screenshot imports arrive as an undoable batch -------------------------------
before_import = client.get("/budget/summary", headers=AUTH).json()["total_spent"]
batch_res = client.post("/transactions/screenshot-import", json={"rows": [
    {"amount": 232, "direction": "debit", "category": "Shopping", "merchant": "Amazon Pay Gift Card", "occurred_on": "2026-08-21"},
    {"amount": 344, "direction": "debit", "category": "Food & Dining", "merchant": "Popeyes", "occurred_on": "2026-08-21"},
    # A GPay "Self transfer" row, sent as a plain expense the way the client
    # once did. Booking this as spending overstated a real month by Rs 10,000.
    {"amount": 10000, "direction": "debit", "category": "Transfer", "merchant": "Self transfer"},
]}, headers=AUTH).json()
check("a screenshot imports as one batch", batch_res["added"] == 3, batch_res)
check("the batch reports only what counts as spending", batch_res["total"] == 576.0, batch_res)
after_import = client.get("/budget/summary", headers=AUTH).json()["total_spent"]
check("a self-transfer in a screenshot never becomes spending",
      round(after_import - before_import, 2) == 576.0, {"before": before_import, "after": after_import})

imported = [t for t in client.get("/transactions", headers=AUTH).json()
            if t.get("import_batch") == batch_res["batch"]]
check("imported rows are tagged as coming from a screenshot",
      len(imported) == 3 and all(t["source"] == "screenshot" for t in imported), imported)
check("the self-transfer row is filed as a transfer, not an expense",
      next(t for t in imported if t["amount"] == 10000)["kind"] == "transfer", imported)

listed = client.get("/imports", headers=AUTH).json()
check("the import is listed for review",
      any(b["batch"] == batch_res["batch"] and b["count"] == 3 for b in listed), listed)

# Something logged by hand between the import and the undo must survive it -
# the whole reason the batch id exists.
keeper = client.post("/transactions/manual",
                     json={"amount": 55, "direction": "debit", "category": "Food & Dining", "merchant": "KEEP ME"},
                     headers=AUTH).json()
undo = client.delete(f"/imports/{batch_res['batch']}", headers=AUTH).json()
check("undoing an import removes exactly its own rows", undo["removed"] == 3, undo)
check("undo restores the previous total",
      client.get("/budget/summary", headers=AUTH).json()["total_spent"] == round(before_import + 55, 2))
check("undo leaves everything else alone",
      any(t["id"] == keeper["id"] for t in client.get("/transactions", headers=AUTH).json()))
check("undoing the same import twice -> 404",
      client.delete(f"/imports/{batch_res['batch']}", headers=AUTH).status_code == 404)
client.delete(f"/transactions/{keeper['id']}", headers=AUTH)
check("a screenshot row dated in the future is refused",
      client.post("/transactions/screenshot-import",
                  json={"rows": [{"amount": 10, "direction": "debit", "category": "Other", "occurred_on": "2099-01-01"}]},
                  headers=AUTH).status_code == 422)
check("an empty screenshot import is refused",
      client.post("/transactions/screenshot-import", json={"rows": []}, headers=AUTH).status_code == 422)

# --- recurring payments: expected this month, and whether they've happened --------
_today = _local_now()


def _month_back(n):
    y, m = _today.year, _today.month
    for _ in range(n):
        m -= 1
        if m < 1:
            m, y = 12, y - 1
    return y, m


for _n in (1, 2):
    _y, _m = _month_back(_n)
    client.post("/transactions/manual", json={
        "amount": 2000, "direction": "debit", "category": "Other",
        "merchant": "MAID SALARY", "occurred_on": f"{_y:04d}-{_m:02d}-05"}, headers=AUTH)
    client.post("/transactions/manual", json={
        "amount": 874, "direction": "debit", "category": "Rent",
        "merchant": "WWW RENTOMOJO COM", "occurred_on": f"{_y:04d}-{_m:02d}-02"}, headers=AUTH)
# Rentomojo has already been paid this month; the maid has not.
paid_this_month = client.post("/transactions/manual", json={
    "amount": 874, "direction": "debit", "category": "Rent",
    "merchant": "WWW RENTOMOJO COM"}, headers=AUTH).json()
# Seen once only - must not be called recurring on the strength of one sighting.
once = client.post("/transactions/manual", json={
    "amount": 300, "direction": "debit", "category": "Shopping",
    "merchant": "ONE OFF SHOP"}, headers=AUTH).json()

rec = {r["merchant"]: r for r in client.get("/stats/recurring", headers=AUTH).json()}
check("a payment seen in several months is detected", "MAID SALARY" in rec, list(rec))
check("...with the amount it usually is", rec.get("MAID SALARY", {}).get("typical_amount") == 2000.0, rec.get("MAID SALARY"))
check("...and the day it usually lands", rec.get("MAID SALARY", {}).get("typical_day") == 5, rec.get("MAID SALARY"))
check("something paid already this month is marked paid",
      rec.get("WWW RENTOMOJO COM", {}).get("status") == "paid", rec.get("WWW RENTOMOJO COM"))
_expected_status = "overdue" if _today.day - 5 > 3 else "due"
check(f"something not yet paid reads as '{_expected_status}' on day {_today.day}",
      rec.get("MAID SALARY", {}).get("status") == _expected_status, rec.get("MAID SALARY"))
check("...counting the days from today",
      rec.get("MAID SALARY", {}).get("days_until") == 5 - _today.day, rec.get("MAID SALARY"))
check("a one-off purchase is not called recurring", "ONE OFF SHOP" not in rec, list(rec))

# Two months is the bar, and the current month counts towards it. Requiring
# two months BEFORE this one was the first cut, and it needed three months of
# history before anything appeared at all - against the two real months in
# production it returned an empty list, the feature silently doing nothing.
_ly, _lm = _month_back(1)
_last_month_only = client.post("/transactions/manual", json={
    "amount": 7500, "direction": "debit", "category": "Health",
    "merchant": "KG FITNESS", "occurred_on": f"{_ly:04d}-{_lm:02d}-08"}, headers=AUTH).json()
_this_month_too = client.post("/transactions/manual", json={
    "amount": 7500, "direction": "debit", "category": "Health",
    "merchant": "KG FITNESS"}, headers=AUTH).json()
rec2 = {r["merchant"]: r for r in client.get("/stats/recurring", headers=AUTH).json()}
check("one previous month plus this one is enough to count as recurring",
      "KG FITNESS" in rec2, list(rec2))
check("...and it reads as paid", rec2.get("KG FITNESS", {}).get("status") == "paid", rec2.get("KG FITNESS"))
# This month's own sighting must not inform the date it is judged against -
# that is circular. The expected day comes from last month's payment (the 8th),
# not from today.
check("the expected day ignores this month's own payment",
      rec2.get("KG FITNESS", {}).get("typical_day") == 8, rec2.get("KG FITNESS"))
client.delete(f"/transactions/{_this_month_too['id']}", headers=AUTH)
rec3 = {r["merchant"]: r for r in client.get("/stats/recurring", headers=AUTH).json()}
check("with this month's payment removed it stops counting as recurring",
      "KG FITNESS" not in rec3, list(rec3))
client.delete(f"/transactions/{_last_month_only['id']}", headers=AUTH)
_statuses = [r["status"] for r in client.get("/stats/recurring", headers=AUTH).json()]
check("overdue items sort to the front",
      _statuses == sorted(_statuses, key=lambda st: {"overdue": 0, "due": 1, "paid": 2}[st]), _statuses)
client.delete(f"/transactions/{once['id']}", headers=AUTH)
client.delete(f"/transactions/{paid_this_month['id']}", headers=AUTH)

# --- full export, for the nightly backup ------------------------------------------
dump = client.get("/export/all", headers=AUTH).json()
for _table in ("transactions", "budgets", "payees", "vehicles", "fuel_fills", "todos", "lending_reminders"):
    check(f"export includes {_table}", _table in dump, list(dump))
check("export counts match the rows it carries",
      all(dump["counts"][t] == len(dump[t]) for t in dump["counts"]), dump["counts"])
check("export carries real transactions", len(dump["transactions"]) > 0, dump["counts"])
check("exported timestamps are serialised, not dropped",
      isinstance(dump["transactions"][0]["created_at"], str), dump["transactions"][0])
# The Gmail refresh token is a live credential, not data. A backup file is
# exactly the wrong place for one - especially with a public repository.
check("export deliberately excludes the Gmail refresh token", "gmail_auth" not in dump, list(dump))
check("no OAuth refresh token anywhere in the export",
      "refresh_token" not in json.dumps(dump), "found a refresh_token key")
check("export needs auth", client.get("/export/all").status_code == 401)


# --- Gemini's "try again shortly" is retried, its "never" is not ------------------
# Google's own 503 body says: "This model is currently experiencing high demand.
# Spikes in demand are usually temporary. Please try again later." That hit a
# real screenshot upload seconds after an identical one succeeded. Surfacing it
# as a hard failure means re-picking the file and sitting through the whole scan
# again - for something that normally clears in a second.
import io  # noqa: E402
import urllib.error  # noqa: E402

import receipt_scan  # noqa: E402

receipt_scan._RETRY_DELAYS = (0.0, 0.0)  # no real waiting inside the test


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _http_error(code):
    return urllib.error.HTTPError("https://x", code, "boom", {}, io.BytesIO(b"{}"))


def _urlopen_failing(times, code, then=b'{"ok": true}'):
    """Fails `times` times with `code`, then succeeds. Counts its own calls."""
    state = {"calls": 0}

    def fake(_req, timeout=None):
        state["calls"] += 1
        if state["calls"] <= times:
            raise _http_error(code)
        return _FakeResponse(then)

    return fake, state


_original_urlopen = receipt_scan.urllib.request.urlopen

fake, state = _urlopen_failing(1, 503)
receipt_scan.urllib.request.urlopen = fake
check("a 503 from Gemini is retried rather than surfaced",
      receipt_scan._post_with_retry(object(), 5) == {"ok": True} and state["calls"] == 2, state)

fake, state = _urlopen_failing(2, 429)
receipt_scan.urllib.request.urlopen = fake
check("a rate limit is retried too", receipt_scan._post_with_retry(object(), 5) == {"ok": True}, state)

# Three attempts total, not an unbounded loop - the user is waiting.
fake, state = _urlopen_failing(99, 503)
receipt_scan.urllib.request.urlopen = fake
try:
    receipt_scan._post_with_retry(object(), 5)
    check("persistent failure eventually gives up", False, "no error raised")
except urllib.error.HTTPError:
    check("persistent failure eventually gives up", True)
check("...after exactly three attempts, not more", state["calls"] == 3, state)

# A bad request or a bad key fails identically forever; retrying only wastes
# the user's time and Google's quota.
fake, state = _urlopen_failing(99, 400)
receipt_scan.urllib.request.urlopen = fake
try:
    receipt_scan._post_with_retry(object(), 5)
except urllib.error.HTTPError:
    pass
check("a 400 is NOT retried", state["calls"] == 1, state)

fake, state = _urlopen_failing(99, 403)
receipt_scan.urllib.request.urlopen = fake
try:
    receipt_scan._post_with_retry(object(), 5)
except urllib.error.HTTPError:
    pass
check("a bad API key is NOT retried", state["calls"] == 1, state)

# A timeout already waited the full budget; spending it again turns a slow scan
# into a very slow one.
_timeouts = {"calls": 0}


def _always_timeout(_req, timeout=None):
    _timeouts["calls"] += 1
    raise urllib.error.URLError(TimeoutError("timed out"))


receipt_scan.urllib.request.urlopen = _always_timeout
try:
    receipt_scan._post_with_retry(object(), 5)
except urllib.error.URLError:
    pass
check("a timeout is NOT retried", _timeouts["calls"] == 1, _timeouts)

receipt_scan.urllib.request.urlopen = _original_urlopen


# --- the lending list is ordered the same way every time -------------------------
# Python's sort is stable, so equal balances kept whatever order the database
# happened to return rows in - which is undefined, and genuinely differs between
# engines: a restored SQLite copy of the production data listed two settled
# friends in the opposite order to Postgres. On screen that is a card that
# reshuffles between refreshes for no visible reason.
for _who, _amt in (("Zara", 100), ("Aditya", 100), ("Meera", 900)):
    client.post("/transactions/manual",
                json={"amount": _amt, "direction": "debit", "kind": "friend", "merchant": _who},
                headers=AUTH)
_order = [p["person"] for p in client.get("/lending", headers=AUTH).json()]
_probe = [p for p in _order if p in ("Zara", "Aditya", "Meera")]
check("the biggest debt is listed first", _probe[0] == "Meera", _probe)
check("equal balances fall back to name order, not database order",
      _probe[1:] == ["Aditya", "Zara"], _probe)
for _t in client.get("/transactions", headers=AUTH).json():
    if _t["merchant"] in ("Zara", "Aditya", "Meera"):
        client.delete(f"/transactions/{_t['id']}", headers=AUTH)


# --- the offline path, which is what survives an expired API key ------------------
# Real spending here is overwhelmingly small local businesses that no brand list
# will ever contain - but they say what they are in their own name. Matching
# that costs nothing, works offline, and is the difference between needing an AI
# call and not. Measured against the real merchant history, this took the share
# handled with no API call at all from 19 of 54 counterparties to 28.
from categorizer import CATEGORIES as _CATS  # noqa: E402
from categorizer import OBVIOUS_MERCHANTS, _rule_match  # noqa: E402

for _name, _expected in [
    ("MOHANDAS JUICE SHOP", "Food & Dining"),
    ("Ss Hyderabad Biriyani Peravallur", "Food & Dining"),
    ("MANAM COFFEE CO", "Food & Dining"),
    ("Ajantha Backers-PNS", None),          # "backers", not "bakers" - not a match
    ("GEETHAM DINE IN 1", "Food & Dining"),
    ("FRESH SUPERMARKET PERAMBUR C1", "Groceries"),
    ("Spectrum mall parking", "Transport"),
    ("Lans Service Station", "Transport"),
    ("MEDPLUS PHARMACY", "Health"),
    ("HARI HARAN AGENCIES 3", None),        # says nothing about what it sells
    ("R MANOHARAN", None),                  # a person's name must never rule-match
    ("VARSHA RS", None),
]:
    check(f"offline rule reads {_name[:28]!r}", _rule_match(_name, "") == _expected,
          f"got {_rule_match(_name, '')!r}, expected {_expected!r}")

# A rule match is treated as confident and never reaches the review queue, so a
# rule pointing at a category that doesn't exist would file spending into a
# bucket no screen can show.
_bad = [(k, v) for k, v in OBVIOUS_MERCHANTS.items() if v not in _CATS]
check("every offline rule maps to a real category", not _bad, _bad)

# Longest match wins, so a specific name beats a generic word. With
# first-match-by-dict-order the answer depended on where a key happened to sit
# in the literal, which stopped being harmless the moment generic trade words
# joined the brand names.
OBVIOUS_MERCHANTS["zzz test generic"] = "Other"
OBVIOUS_MERCHANTS["zzz test generic specific"] = "Health"
check("the longer, more specific keyword wins",
      _rule_match("ZZZ TEST GENERIC SPECIFIC LTD", "") == "Health",
      _rule_match("ZZZ TEST GENERIC SPECIFIC LTD", ""))
check("...and the generic one still matches on its own",
      _rule_match("ZZZ TEST GENERIC LTD", "") == "Other")
del OBVIOUS_MERCHANTS["zzz test generic"], OBVIOUS_MERCHANTS["zzz test generic specific"]

# --- an expired Azure subscription must not tax every transaction ----------------
# The key behind this is on a subscription that expires. When it does, every
# unknown merchant would pay a full round trip to an endpoint certain to refuse
# it before falling through to Gemini - classification quietly getting slower
# the day a deadline passes, with nothing on screen to say why.
import categorizer as _cat  # noqa: E402

_cat._azure_key = "expired-key"
_cat._azure_endpoint = "https://example.invalid"
_cat._azure_deployment = "gpt-5-mini-1"


def _azure_calls_counting(code):
    state = {"calls": 0}

    def fake(_req, timeout=None):
        state["calls"] += 1
        raise urllib.error.HTTPError("https://x", code, "nope", {}, io.BytesIO(b"{}"))

    return fake, state


_saved_urlopen = _cat.urllib.request.urlopen

_cat._azure_disabled = False
fake, state = _azure_calls_counting(401)
_cat.urllib.request.urlopen = fake
for _ in range(5):
    _cat._azure_categorize("SOME SHOP", "")
check("an expired Azure key is tried once, not once per transaction",
      state["calls"] == 1, state)
check("...and the provider marks itself disabled", _cat._azure_disabled is True)

# A rate limit or a server blip is temporary. Disabling the provider for the
# life of the process over one of those would throw away the good key.
_cat._azure_disabled = False
fake, state = _azure_calls_counting(429)
_cat.urllib.request.urlopen = fake
for _ in range(3):
    _cat._azure_categorize("SOME SHOP", "")
check("a rate limit does NOT disable Azure", state["calls"] == 3 and _cat._azure_disabled is False, state)

_cat._azure_disabled = False
fake, state = _azure_calls_counting(500)
_cat.urllib.request.urlopen = fake
for _ in range(3):
    _cat._azure_categorize("SOME SHOP", "")
check("a server error does NOT disable Azure", state["calls"] == 3 and _cat._azure_disabled is False, state)

_cat.urllib.request.urlopen = _saved_urlopen
_cat._azure_key = _cat._azure_endpoint = _cat._azure_deployment = ""
_cat._azure_disabled = False


# --- undated screenshot rows must not skip duplicate detection -------------------
# A row the vision model couldn't read a date for used to skip duplicate
# checking entirely: both branches of the comparison required a truthy date,
# so no date meant no check, no matter how obviously it matched something
# already stored. Confirmed live: a screenshot read 4 of 5 rows correctly and
# missed the date on one, and that one went straight back in as new money the
# next day - an exact repeat of "POP STIX PERIYAR NAGAR" Rs 63.
# "Yesterday" relative to whatever day the test actually runs on, not a
# hardcoded date - the stored row and the undated row must land within the
# app's own real ±1-day window, exactly like the live case that found this.
from datetime import timedelta as _shot_td  # noqa: E402

_shot_today = main.local_now().date()
_shot_yesterday = (_shot_today - _shot_td(days=1)).isoformat()
already = client.post("/transactions/manual", json={
    "amount": 63, "direction": "debit", "category": "Food & Dining",
    "merchant": "POP STIX PERIYAR NAGAR", "occurred_on": _shot_yesterday,
}, headers=AUTH).json()

_undated_rows = [
    {"occurred_on": "", "merchant": "POP STIX PERIYAR NAGAR", "amount": 63.0,
     "direction": "debit", "category": "Food & Dining"},
]
# Call it exactly as the endpoint does — through a real session.
_db = SessionLocal()
try:
    main._flag_duplicates(_undated_rows, _db)
finally:
    _db.close()
check("a row with no date is still checked against what's already stored",
      _undated_rows[0]["already_recorded"] is True, _undated_rows[0])
check("...and says why", _undated_rows[0]["duplicate_reason"] is not None, _undated_rows[0])

# An undated row that genuinely is new money must NOT be flagged just for
# lacking a date — only a real match should trip this.
_new_rows = [
    {"occurred_on": "", "merchant": "SOME BRAND NEW SHOP", "amount": 9999.0,
     "direction": "debit", "category": "Shopping"},
]
_db = SessionLocal()
try:
    main._flag_duplicates(_new_rows, _db)
finally:
    _db.close()
check("an undated row with nothing matching it is NOT flagged",
      _new_rows[0]["already_recorded"] is False, _new_rows[0])

client.delete(f"/transactions/{already['id']}", headers=AUTH)

# --- a "Transfer" guess from the vision model is not proof of a self-transfer ----
# The model has picked "Transfer" for a plain person's name before ("S
# Sadashiva", Rs 120), and screenshot_import() used to trust that category
# alone as proof of a self-transfer — removing real spending from every total
# with no human check at all. Only actual self-transfer LANGUAGE in the
# merchant text is trusted now; everything else goes to Review instead.
spent_before_shot = client.get("/budget/summary", headers=AUTH).json()["total_spent"]
ambiguous = client.post("/transactions/screenshot-import", json={"rows": [
    {"amount": 120, "direction": "debit", "category": "Transfer",
     "merchant": "S Sadashiva", "occurred_on": "2026-08-23"},
]}, headers=AUTH).json()
_ambig_row = next(t for t in client.get("/transactions", headers=AUTH).json()
                   if t["id"] and t.get("import_batch") == ambiguous["batch"])
check("an ambiguous 'Transfer' guess is NOT trusted as a self-transfer",
      _ambig_row["kind"] == "expense", _ambig_row)
check("...it is queued for review instead of guessed either way",
      _ambig_row["needs_review"] is True, _ambig_row)
check("...and does not keep the meaningless 'Transfer' category",
      _ambig_row["category"] == "Uncategorized", _ambig_row)
spent_after_shot = client.get("/budget/summary", headers=AUTH).json()["total_spent"]
check("...so the money is still counted as spending while it waits to be answered",
      round(spent_after_shot - spent_before_shot, 2) == 120.0,
      {"before": spent_before_shot, "after": spent_after_shot})

# A genuine self-transfer must still work exactly as before — no regression.
genuine = client.post("/transactions/screenshot-import", json={"rows": [
    {"amount": 5000, "direction": "debit", "category": "Transfer",
     "merchant": "Self transfer (to my other account)", "occurred_on": "2026-08-23"},
]}, headers=AUTH).json()
_genuine_row = next(t for t in client.get("/transactions", headers=AUTH).json()
                     if t.get("import_batch") == genuine["batch"])
check("real self-transfer language IS still trusted",
      _genuine_row["kind"] == "transfer" and _genuine_row["needs_review"] is False, _genuine_row)
spent_after_genuine = client.get("/budget/summary", headers=AUTH).json()["total_spent"]
check("...and a real self-transfer is still excluded from spending",
      spent_after_genuine == spent_after_shot, {"after_shot": spent_after_shot, "after_genuine": spent_after_genuine})

# "Lending" gets the same protection — no path currently books a screenshot
# row as a real loan, so a bare "Lending" guess is exactly as untrustworthy as
# "Transfer" and must not silently misfile a category no ordinary answer uses.
lending_guess = client.post("/transactions/screenshot-import", json={"rows": [
    {"amount": 75, "direction": "debit", "category": "Lending",
     "merchant": "R Karthikeyan", "occurred_on": "2026-08-23"},
]}, headers=AUTH).json()
_lend_row = next(t for t in client.get("/transactions", headers=AUTH).json()
                  if t.get("import_batch") == lending_guess["batch"])
check("a bare 'Lending' guess also gets routed to review, not trusted",
      _lend_row["kind"] == "expense" and _lend_row["needs_review"] is True and _lend_row["category"] == "Uncategorized",
      _lend_row)

for _batch in (ambiguous["batch"], genuine["batch"], lending_guess["batch"]):
    client.delete(f"/imports/{_batch}", headers=AUTH)


# --- report ---------------------------------------------------------------------
# Must stay the LAST thing in this file. It used to sit in the middle, so the
# checks below it printed PASS/FAIL but could not fail the run — a broken one
# would have exited 0 and reported "All checks passed" two lines earlier.
print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print(f"All {len(passed)} checks passed.")

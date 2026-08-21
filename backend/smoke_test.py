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
from categorizer import parse_alert_date, parse_sms  # noqa: E402

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

r = client.post("/sms/ingest", json={"text": "INR 1,250.50 spent on HDFC Card x1234 at MADHURA SWEETS on 2026-08-15"}, headers=AUTH).json()
check("unknown merchant -> needs review", r["transaction"]["needs_review"] is True, r["transaction"])
check("merchant extracted", r["transaction"]["merchant"] == "MADHURA SWEETS", r["transaction"])
check("comma amount parsed", r["transaction"]["amount"] == 1250.50, r["transaction"])
review_id = r["transaction"]["id"]

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

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("All checks passed.")

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

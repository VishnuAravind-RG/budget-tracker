"""Reads a photo of a receipt/payment screenshot via Gemini's vision API and
extracts a transaction. Uses plain HTTP (urllib), not the google-generativeai
SDK — one more dependency isn't worth it for a single REST call, matching how
the rest of this backend avoids client libraries it doesn't strictly need.

Separate from categorizer.py's Claude usage deliberately: that's the SMS
fast-path (free rule match first, AI only for the leftover unknowns).
This is a heavier vision call that only runs when the user explicitly
attaches an image — never automatically, never per-keystroke.
"""

import base64
import json
import os
import re
import urllib.error
import urllib.request

from categorizer import CATEGORIES

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
_api_key = os.getenv("GEMINI_API_KEY", "").strip()

ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


def gemini_configured() -> bool:
    return bool(_api_key)


SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "amount": {"type": "NUMBER", "description": "The total amount paid, as a plain number"},
        "merchant": {"type": "STRING", "description": "Merchant/payee name, or a short description if none is visible"},
        "direction": {"type": "STRING", "enum": ["debit", "credit"]},
        "category": {"type": "STRING", "enum": CATEGORIES},
        "confident": {"type": "BOOLEAN", "description": "False if the image is blurry, not a receipt, or the amount is unclear"},
    },
    "required": ["amount", "merchant", "direction", "category", "confident"],
}


class ReceiptScanError(Exception):
    pass


def scan_receipt(image_bytes: bytes, mime_type: str, note: str | None = None) -> dict:
    """Returns {amount, merchant, direction, category, confident}.

    `note` is free text the user typed alongside the image (e.g. "this was
    actually for a friend's birthday, categorise as Entertainment") — passed
    to the model as authoritative context that overrides what the image alone
    would suggest, exactly like the user correcting a human assistant.
    """
    if not gemini_configured():
        raise ReceiptScanError("GEMINI_API_KEY is not configured")

    b64 = base64.b64encode(image_bytes).decode("ascii")

    prompt = (
        "This is a photo or screenshot of a receipt, bill, or payment confirmation. "
        "Extract the transaction. If the user has given you additional context below, "
        "treat it as authoritative — it overrides what the image alone would suggest, "
        "especially for category.\n\n"
        f"User context: {note.strip() if note else '(none given)'}\n\n"
        f"Category must be exactly one of: {', '.join(CATEGORIES)}.\n"
        "direction is 'debit' unless this is clearly a refund or money received.\n"
        "Set confident=false if the image isn't a receipt/payment, is unreadable, "
        "or the amount can't be determined with reasonable certainty."
    )

    body = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": b64}},
            ],
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": SCHEMA,
        },
    }

    req = urllib.request.Request(
        f"{ENDPOINT}?key={_api_key}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise ReceiptScanError(f"Gemini request failed ({e.code}): {detail}") from e
    except urllib.error.URLError as e:
        raise ReceiptScanError(f"Couldn't reach Gemini: {e.reason}") from e
    except TimeoutError as e:
        # A read timeout (as opposed to a connect timeout) surfaces as a bare
        # TimeoutError here, not wrapped in URLError — confirmed in
        # production: a slow Gemini response during a demand spike leaked
        # this straight past the two handlers above as an unhandled
        # exception, returning a raw 500 instead of a clean error message.
        raise ReceiptScanError("Gemini took too long to respond — try again in a moment") from e
    except json.JSONDecodeError as e:
        raise ReceiptScanError("Gemini returned a response that wasn't valid JSON") from e

    try:
        text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        parsed = json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise ReceiptScanError("Gemini returned an unreadable response") from e

    if parsed.get("category") not in CATEGORIES:
        parsed["category"] = "Other"
    if parsed.get("direction") not in ("debit", "credit"):
        parsed["direction"] = "debit"
    try:
        parsed["amount"] = float(parsed.get("amount") or 0)
    except (TypeError, ValueError):
        parsed["amount"] = 0.0

    return parsed


STATEMENT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "transactions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "occurred_on": {
                        "type": "STRING",
                        "description": "Date as YYYY-MM-DD. Rows in these apps usually show only a day and month "
                                       "('21 August'); take the year from the screen's own header if one is shown, "
                                       "otherwise from the reference year given below. Empty string if truly absent.",
                    },
                    "merchant": {"type": "STRING", "description": "Payee name exactly as shown"},
                    "amount": {"type": "NUMBER", "description": "Plain number, no currency symbol or separators"},
                    "direction": {"type": "STRING", "enum": ["debit", "credit"]},
                    "category": {"type": "STRING", "enum": CATEGORIES},
                },
                "required": ["occurred_on", "merchant", "amount", "direction", "category"],
            },
        },
    },
    "required": ["transactions"],
}


def scan_statement(image_bytes: bytes, mime_type: str, reference_year: int) -> list[dict]:
    """Reads a screenshot of a transaction LIST — GPay/PhonePe history, a bank
    statement — and returns every row it can see.

    Exists because the alert-based capture is genuinely incomplete: a real
    GPay history showed six payments (Rs 9,691, including a Rs 7,500 gym fee)
    that the bank never emailed or texted at all. There is no consumer API
    for any of these apps, so a screenshot is the only machine-readable form
    of that data a person can actually get hold of.

    Returns a preview only — nothing is booked. The caller shows the rows for
    confirmation, because a mis-read amount silently entering the ledger is
    far worse than one the user glanced at first.
    """
    if not gemini_configured():
        raise ReceiptScanError("GEMINI_API_KEY is not configured")

    b64 = base64.b64encode(image_bytes).decode("ascii")
    prompt = (
        "This is a screenshot of a list of transactions from a payments app or bank.\n\n"
        "Extract EVERY transaction row visible. Do not invent rows that are cut off at the "
        "edge and unreadable, and do not merge two rows into one.\n\n"
        f"Reference year for dates with no year shown: {reference_year}.\n"
        "direction is 'debit' for money going out, 'credit' for money coming in. Most rows "
        "in a payments history are debits.\n"
        "Pick the best category from the list for each row based on the payee name.\n"
        "A running total or balance shown in a header is NOT a transaction — skip it."
    )

    body = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": b64}},
            ],
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": STATEMENT_SCHEMA,
        },
    }

    req = urllib.request.Request(
        f"{ENDPOINT}?key={_api_key}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        # Longer than scan_receipt's: a list of a dozen rows is a much bigger
        # generation than a single receipt's five fields.
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise ReceiptScanError(f"Gemini request failed ({e.code}): {detail}") from e
    except urllib.error.URLError as e:
        raise ReceiptScanError(f"Couldn't reach Gemini: {e.reason}") from e
    except TimeoutError as e:
        raise ReceiptScanError("Gemini took too long on that screenshot — try a shorter one") from e
    except json.JSONDecodeError as e:
        raise ReceiptScanError("Gemini returned a response that wasn't valid JSON") from e

    try:
        text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        parsed = json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise ReceiptScanError("Gemini returned an unreadable response") from e

    rows = []
    for item in parsed.get("transactions", []):
        try:
            amount = float(item.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue  # a row with no readable amount is not usable
        category = item.get("category")
        date = (item.get("occurred_on") or "").strip()
        rows.append({
            "occurred_on": date if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) else None,
            "merchant": (item.get("merchant") or "").strip()[:80] or "Unknown",
            "amount": amount,
            "direction": item.get("direction") if item.get("direction") in ("debit", "credit") else "debit",
            "category": category if category in CATEGORIES else "Other",
        })
    return rows

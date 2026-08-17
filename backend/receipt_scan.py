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
import urllib.error
import urllib.request

from categorizer import CATEGORIES

MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
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

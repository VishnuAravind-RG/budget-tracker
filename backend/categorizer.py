"""SMS parsing + categorization: free rule-based fast path, then Claude if
configured, then Gemini's free tier — in that order, cheapest first."""

import json
import os
import re
import urllib.error
import urllib.request

from anthropic import Anthropic

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5")

_api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
client = Anthropic(api_key=_api_key) if _api_key else None

# Free-tier fallback: most personal deployments of this app won't want to
# pay for Claude API calls just to categorise "Ss Hyderabad Biriyani" as
# Food & Dining — Gemini's free tier does the same job for nothing, and
# receipt_scan.py already needs a GEMINI_API_KEY for photo scanning, so
# there's a good chance one's already configured.
_gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
_gemini_model = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
_GEMINI_ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{_gemini_model}:generateContent"

CATEGORIES = [
    "Food & Dining", "Groceries", "Transport", "Shopping",
    "Bills & Utilities", "Entertainment", "Health", "Rent",
    "Investment", "Transfer", "Income", "Lending", "Other",
]

# Fast, free, obvious matches -> skip the AI call entirely for common merchants.
OBVIOUS_MERCHANTS = {
    "swiggy": "Food & Dining", "zomato": "Food & Dining", "dominos": "Food & Dining",
    "starbucks": "Food & Dining", "eatsure": "Food & Dining", "dunzo": "Food & Dining",
    "blinkit": "Groceries", "zepto": "Groceries", "bigbasket": "Groceries",
    "dmart": "Groceries", "instamart": "Groceries", "jiomart": "Groceries",
    "uber": "Transport", "ola": "Transport", "rapido": "Transport",
    "irctc": "Transport", "petrol": "Transport", "hpcl": "Transport",
    "iocl": "Transport", "bpcl": "Transport", "fastag": "Transport",
    "redbus": "Transport", "indigo": "Transport", "namma yatri": "Transport",
    "amazon": "Shopping", "flipkart": "Shopping", "myntra": "Shopping",
    "ajio": "Shopping", "nykaa": "Shopping", "meesho": "Shopping",
    "netflix": "Entertainment", "spotify": "Entertainment", "hotstar": "Entertainment",
    "bookmyshow": "Entertainment", "prime video": "Entertainment", "youtube": "Entertainment",
    "pharmeasy": "Health", "apollo": "Health", "practo": "Health", "1mg": "Health",
    "cult": "Health", "netmeds": "Health",
    "airtel": "Bills & Utilities", "jio": "Bills & Utilities", "vodafone": "Bills & Utilities",
    "electricity": "Bills & Utilities", "bescom": "Bills & Utilities", "gas": "Bills & Utilities",
    "zerodha": "Investment", "groww": "Investment", "upstox": "Investment",
    "kuvera": "Investment", "coin": "Investment", "sip": "Investment",
}

# Junk SMS that mention money but aren't transactions.
NON_TRANSACTIONAL = re.compile(
    r"\b(otp|one[- ]time password|is your (?:verification|login) code|"
    r"will be debited|has been requested|payment request|requesting|"
    r"e-?mandate|autopay.*(?:scheduled|due)|reminder|due on|"
    r"apply now|pre-?approved|offer|cashback of|congratulations|win)\b",
    re.IGNORECASE,
)

AMOUNT_RE = re.compile(
    r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d{1,2})?)|"
    r"([\d,]+(?:\.\d{1,2})?)\s*(?:rs\.?|inr|rupees)",
    re.IGNORECASE,
)
DEBIT_RE = re.compile(r"\b(debited|spent|paid|withdrawn|purchase|sent|deducted)\b", re.IGNORECASE)
CREDIT_RE = re.compile(r"\b(credited|received|refund(?:ed)?|deposited|cashback)\b", re.IGNORECASE)

MERCHANT_PATTERNS = [
    re.compile(r"\bVPA\s+([\w.\-]+@[\w.\-]+)", re.IGNORECASE),
    re.compile(r"\b(?:to|at|towards|for|from)\s+([\w.\-]+@[\w.\-]+)", re.IGNORECASE),
    # "Sent Rs.X\nFrom <bank> A/C *nnnn\nTo <name>\n..." — a common bank-SMS
    # shape for a UPI send, where "From" names the SENDER's own bank/account,
    # not a counterparty. Tried before the general pattern below so "To
    # <name>" wins over an earlier "From <bank>" match in the same text —
    # re.search always returns the leftmost match, and "From" precedes "To"
    # in this template, so without this the sender's bank got captured as
    # the merchant instead of who the money actually went to.
    re.compile(
        r"\bto\s+"
        r"(?!(?:rs\.?|inr|₹)\b)"
        r"([A-Za-z][A-Za-z0-9&'.\- ]{1,38}?)"
        r"(?=\s+(?:on|ref|upi|via|dated|txn|a/c|account|bal|avl|towards)\b|[.,;()\n]|$)",
        re.IGNORECASE,
    ),
    re.compile(
        # "...debited for SWIGGY on 12-05" / "...spent at AMAZON on ..."
        # The lookahead stops it starting on a currency token, so
        # "debited for Rs 1000 towards NETFLIX" doesn't capture the amount.
        r"\b(?:at|to|towards|for|in favou?r of|from)\s+"
        r"(?!(?:rs\.?|inr|₹)\b)"
        r"([A-Za-z][A-Za-z0-9&'.\- ]{1,38}?)"
        r"(?=\s+(?:on|ref|upi|via|dated|txn|a/c|account|bal|avl|towards)\b|[.,;()\n]|$)",
        re.IGNORECASE,
    ),
]

# Words that mean the regex grabbed boilerplate rather than a merchant name.
MERCHANT_STOPWORDS = {
    "your", "a/c", "ac", "account", "card", "bank", "upi", "the", "you",
    "rs", "inr", "txn", "transaction", "payment", "purchase",
}


def _clean_merchant(raw: str) -> str:
    name = re.sub(r"\s+", " ", raw).strip(" .,-")
    if not name or name.lower() in MERCHANT_STOPWORDS or len(name) < 2:
        return ""
    # Reject captures that are really just an amount or a reference number.
    if not re.search(r"[A-Za-z]{2}", name):
        return ""
    return name[:60]


def parse_sms(text: str) -> dict:
    """Extract amount, direction and merchant from a typical Indian bank SMS.

    `is_transaction` is False for OTPs, promos and payment reminders — the caller
    should drop those instead of storing a zero-rupee row.
    """
    amount = 0.0
    match = AMOUNT_RE.search(text)
    if match:
        raw_amount = match.group(1) or match.group(2)
        try:
            amount = float(raw_amount.replace(",", ""))
        except ValueError:
            amount = 0.0

    is_credit = bool(CREDIT_RE.search(text))
    is_debit = bool(DEBIT_RE.search(text))
    direction = "credit" if (is_credit and not is_debit) else "debit"

    merchant = ""
    # HDFC's "account credited" email template ("...successfully credited to
    # your HDFC Bank account ending in 9393. Transaction Details: ...") names
    # no counterparty at all — the generic merchant pattern below would
    # otherwise grab boilerplate prose ("inform you that Rs") as if it were
    # one, since "to your ... account" matches its "to <name>" shape.
    if "successfully credited to your" not in text.lower():
        for pattern in MERCHANT_PATTERNS:
            found = pattern.search(text)
            if found:
                merchant = _clean_merchant(found.group(1))
                if merchant:
                    break

    is_transaction = (
        amount > 0
        and (is_debit or is_credit)
        and not NON_TRANSACTIONAL.search(text)
    )

    return {
        "amount": amount,
        "direction": direction,
        "merchant": merchant or "Unknown",
        "is_transaction": is_transaction,
    }


_UPI_ID_RE = re.compile(r"^[\w.\-]+@[\w.\-]+$")


def payee_key_for(merchant: str) -> str | None:
    """Stable lookup key for 'who is this' — a real UPI id when the extracted
    merchant looks like one (arjun@ybl), or a normalised `name:<text>` key for
    a card swipe with no VPA (name:xyz traders). Mirrors identityKeyFor from
    the client-side prototype — same reasoning: a card swipe has no VPA to key
    off, but the merchant name is still stable across repeat visits."""
    if not merchant or merchant == "Unknown":
        return None
    if _UPI_ID_RE.match(merchant):
        return merchant.lower()
    normalized = re.sub(r"\s+", " ", merchant).strip().lower()
    return f"name:{normalized}" if len(normalized) >= 2 else None


def _rule_match(merchant: str, raw_text: str) -> str | None:
    haystack = f"{merchant} {raw_text}".lower()
    for key, category in OBVIOUS_MERCHANTS.items():
        if key in haystack:
            return category
    return None


def _gemini_categorize(merchant: str, raw_text: str) -> dict | None:
    """Same job as the Claude branch below, via Gemini's free tier instead.
    Returns None on any failure (bad key, network, malformed response,
    rate-limited — Gemini's free tier caps requests/minute, see
    _recategorize_pending_sync's pacing) so the caller falls through to the
    review queue rather than losing the transaction — this is a nice-to-have
    automation, not something that should ever be able to break ingestion."""
    if not _gemini_key:
        return None

    body = {
        "contents": [{
            "parts": [{
                "text": (
                    "Classify this Indian bank transaction into exactly one category.\n\n"
                    f"Merchant: {merchant}\nSMS text: {raw_text}\n\n"
                    "Set confident=false if the merchant is unrecognisable or the text is "
                    "too ambiguous to categorise reliably."
                ),
            }],
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "category": {"type": "STRING", "enum": CATEGORIES},
                    "confident": {"type": "BOOLEAN"},
                },
                "required": ["category", "confident"],
            },
        },
    }
    req = urllib.request.Request(
        f"{_GEMINI_ENDPOINT}?key={_gemini_key}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        parsed = json.loads(text)
        category = parsed.get("category", "Other")
        if category not in CATEGORIES:
            category = "Other"
        return {"category": category, "confident": bool(parsed.get("confident", False))}
    except Exception:
        return None


def categorize(merchant: str, raw_text: str, direction: str = "debit") -> dict:
    """Returns {'category', 'needs_review', 'source'}. Cheap path first, AI second.

    `source` tells the caller *why* — main.py needs this to decide whether to
    also ask "who is this / merchant, friend, wallet, or my own account?":
    a hardcoded rule match is a definitively known brand (never ask), but an
    AI-confident guess on a brand-new counterparty still might be a friend or
    a wallet the AI has no way to know about — see _ingest()'s payee handling.
    """
    hit = _rule_match(merchant, raw_text)
    if hit:
        return {"category": hit, "needs_review": False, "source": "rule"}

    if direction == "credit":
        # Money coming in isn't spending — don't burn an AI call or a review slot.
        return {"category": "Income", "needs_review": False, "source": "income"}

    if client is None:
        # No Claude key — try Gemini's free tier before giving up to a review slot.
        gemini_result = _gemini_categorize(merchant, raw_text)
        if gemini_result is None:
            return {"category": "Uncategorized", "needs_review": True, "source": "no_ai"}
        return {
            "category": gemini_result["category"],
            "needs_review": not gemini_result["confident"],
            "source": "ai_confident" if gemini_result["confident"] else "ai_unsure",
        }

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=200,
            thinking={"type": "disabled"},
            output_config={
                "effort": "low",
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string", "enum": CATEGORIES},
                            "confident": {"type": "boolean"},
                        },
                        "required": ["category", "confident"],
                        "additionalProperties": False,
                    },
                },
            },
            messages=[{
                "role": "user",
                "content": (
                    "Classify this Indian bank transaction into exactly one category.\n\n"
                    f"Merchant: {merchant}\n"
                    f"SMS text: {raw_text}\n\n"
                    "Set confident=false if the merchant is unrecognisable or the text is "
                    "too ambiguous to categorise reliably."
                ),
            }],
        )
        text = next(b.text for b in response.content if b.type == "text")
        parsed = json.loads(text)
        category = parsed.get("category", "Other")
        if category not in CATEGORIES:
            category = "Other"
        confident = parsed.get("confident", False)
        return {
            "category": category,
            "needs_review": not confident,
            "source": "ai_confident" if confident else "ai_unsure",
        }
    except Exception:
        # Never let a categorization failure lose the transaction.
        return {"category": "Uncategorized", "needs_review": True, "source": "ai_error"}

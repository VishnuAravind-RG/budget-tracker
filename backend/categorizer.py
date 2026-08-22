"""SMS parsing + categorization: free rule-based fast path, then Claude if
configured, then Azure OpenAI, then Gemini's free tier as last resort —
Azure went first because Gemini's free daily quota proved too small to
survive even a one-time backlog pass, let alone rely on day to day."""

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
# "gemini-flash-latest" was the previous default here — it silently resolved
# to gemini-3.7-flash, a newer model whose free tier caps out at 20 requests
# *per day*, not per minute. Confirmed live via Google's own error body:
# "GenerateRequestsPerDayPerProjectPerModel-FreeTier ... quotaValue: 20".
# Pinned to an established flash model with a real free allowance instead —
# "latest" auto-upgrading to whatever's newest is exactly how this broke.
_gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
_GEMINI_ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{_gemini_model}:generateContent"

# Second free-ish fallback, tried only if Gemini itself is unset or fails
# (e.g. its daily quota is exhausted, as happened during a large one-time
# backfill — see the comment above). Azure OpenAI needs three separate
# pieces, not just a key: the resource endpoint and the deployment name
# (the name given to the model when it was deployed in Azure, not
# necessarily matching the underlying model's own name).
_azure_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
_azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
_azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()

CATEGORIES = [
    "Food & Dining", "Groceries", "Transport", "Shopping",
    "Bills & Utilities", "Entertainment", "Health", "Rent",
    "Investment", "Transfer", "Income", "Lending", "Other",
]

# Whether the counterparty is a business or an individual. This is a SEPARATE
# question from the category and it's the one that protects the totals: money
# to a person may be lending, not spending, and must not be auto-counted.
# Only "business" is ever trusted enough to skip the review queue — "person"
# and "unclear" both still go and ask. See _ingest() in main.py.
ENTITY_KINDS = ["business", "person", "unclear"]

_CLASSIFY_PROMPT = (
    "Classify this Indian bank transaction.\n\n"
    "Merchant: {merchant}\nSMS text: {raw_text}\n\n"
    f"category: one of {', '.join(CATEGORIES)}.\n"
    "confident: false if the merchant is unrecognisable or too ambiguous to categorise.\n"
    "entity: is the counterparty a business or an individual person?\n"
    "  - 'business' ONLY if the name clearly identifies a shop, restaurant, company\n"
    "    or service — e.g. 'INIYA MUGIL SOUP', 'GEETHAM DINE IN 1', 'KP SEASON FRUITS'.\n"
    "  - 'person' if it reads as an individual's name, including Indian names with\n"
    "    initials — e.g. 'R MANOHARAN', 'S MEERA', 'VARSHA RS', 'Kamal Pariyar'.\n"
    "  - 'unclear' if you cannot tell, or it is only a UPI handle or a number.\n"
    "Be conservative: answer 'unclear' rather than guessing 'business', because a\n"
    "wrong 'business' silently counts money sent to a friend as spending."
)

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

# Banks that name the payee put it in parentheses right after the VPA:
#   "...towards VPA q743985996@ybl (Ss Hyderabad Biriyani Peravallur) on..."
# That parenthesised name is the only human-readable thing in the message and
# must win over the VPA itself. Preferring the VPA (as this did originally)
# meant every merchant was stored and displayed as "q743985996@ybl", and —
# worse — that opaque string was what got handed to the AI categoriser, which
# then had nothing to work with and dumped almost everything into review.
# The VPA is still captured, but as the *identity key*, which is its real job.
VPA_WITH_NAME_RE = re.compile(
    r"\bVPA\s+([\w.\-]+@[\w.\-]+)\s*\(([^)]{2,60})\)", re.IGNORECASE
)
# "VPA arjun@ybl" in a debit alert, but "(VPA: arjun@ybl)" in a credit one —
# the colon form went unmatched, so credits fell through to a blind search for
# the first @-looking token anywhere in the message.
VPA_RE = re.compile(r"\bVPA:?\s+([\w.\-]+@[\w.\-]+)", re.IGNORECASE)
ANY_VPA_RE = re.compile(r"\b([\w.\-]+@[\w.\-]+)")

# Credit alerts name who sent the money, which debit alerts never do:
#   "b. Sender: VISHNU ARAVIND R G (VPA: rgvishnuaravind@oksbi)"
# Without this every credit was stored as "Unknown" — and, worse, money moved
# in from the account holder's OWN other bank looked identical to real income.
# Capturing the sender means that can be answered once ("my account") and
# remembered, exactly like any other payee.
CREDIT_SENDER_RE = re.compile(
    r"(?:Sender|Remitter|Received from|Credited by|From)\s*:?\s*"
    r"([^()\n]{2,60}?)\s*\(\s*VPA:?\s*([\w.\-]+@[\w.\-]+)\s*\)",
    re.IGNORECASE,
)

# The same sender line without the parenthesised VPA. HDFC's own email
# template numbers its fields ("a. Date: ... b. Sender: ... c. Amount: ..."),
# and the VPA is not always among them — three real credits totalling
# Rs 11,912, one of them Rs 10,000, were stored as merchant "Unknown" because
# a sender with no VPA beside it matched nothing at all. A name alone is
# still worth having: it is the difference between "someone sent you 10,000"
# and a blank.
#
# The trailing lookahead stops the capture running into the next lettered
# field of that template ("... b. Sender: ARJUN K c. Amount: ...").
CREDIT_SENDER_NAME_RE = re.compile(
    r"(?:Sender|Remitter|Received from|Credited by)\s*(?:Name)?\s*:\s*"
    r"([A-Za-z][A-Za-z0-9&'.\- ]{1,58}?)"
    r"(?=\s+[a-z]\.\s|\s*[(,;\n]|\.\s|$|\s+(?:on|ref|upi|txn|vpa|a/c|account|amount|dated)\b)",
    re.IGNORECASE,
)

# Which of the account holder's OWN accounts the money landed in. Not a
# counterparty, but the only identifying detail some credit alerts carry at
# all — and "Credit to HDFC ...9393" is a far more honest label for a row
# than "Unknown", which reads like a parse failure and hides the real
# situation: the alert genuinely never said who sent it.
CREDIT_ACCOUNT_TAIL_RE = re.compile(
    r"credited to your\s+([A-Za-z][A-Za-z ]{1,20}?)\s*(?:Bank\s*)?account\s*"
    r"(?:ending in|ending|no\.?|number|x+)?\s*(\d{3,6})",
    re.IGNORECASE,
)

# Not every parenthesis after a VPA is a name — plenty of banks put the
# reference number there instead: "to VPA swiggy@icici (UPI Ref 402913)".
# Booking that as the merchant would be worse than falling back to the VPA.
_REFERENCE_HINT_RE = re.compile(r"\b(?:upi|ref|rrn|txn|transaction)\b", re.IGNORECASE)


def _looks_like_reference(value: str) -> bool:
    if _REFERENCE_HINT_RE.search(value) and re.search(r"\d", value):
        return True
    alnum = re.sub(r"[^A-Za-z0-9]", "", value)
    # Mostly digits is an id, not a shop name — but a real name with a branch
    # number in it ("FRESH SUPERMARKET PERAMBUR C1") stays well under half.
    return bool(alnum) and sum(c.isdigit() for c in alnum) / len(alnum) > 0.5

MERCHANT_PATTERNS = [
    re.compile(r"\bVPA\s+([\w.\-]+@[\w.\-]+)", re.IGNORECASE),
    re.compile(r"\b(?:to|at|towards|for|from)\s+([\w.\-]+@[\w.\-]+)", re.IGNORECASE),
    # "Sent Rs.X\nFrom <bank> A/C *nnnn\nTo <name>\n..." — a common bank-SMS
    # shape for a UPI send, where "From" names the SENDER's own bank/account,
    # not a counterparty. Needed because re.search takes the leftmost match
    # and "From" precedes "To" here, so the general pattern below captured
    # the sender's own bank as the merchant.
    #
    # Anchored to the START OF A LINE, which is what makes it safe. An
    # unanchored \bto\b matched the word "to" absolutely anywhere, including
    # inside HDFC's footer — a real card alert was booked with the merchant
    # "support you in every step of t", lifted from "We're here to support
    # you in every step of the way."
    re.compile(
        r"(?:^|\n)\s*to\s+"
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

    # The VPA is the stable identity, kept separately from the display name —
    # see VPA_WITH_NAME_RE. Captured even when the name wins as `merchant`,
    # so "who is this?" memory still keys off something that never changes.
    upi_match = VPA_RE.search(text) or ANY_VPA_RE.search(text)
    upi_id = upi_match.group(1).lower() if upi_match else None

    merchant = ""
    # A credit alert names who sent the money — take that, and its VPA as the
    # identity, so an incoming transfer from the account holder's own other
    # bank can be recognised rather than counted as income forever.
    sender = CREDIT_SENDER_RE.search(text)
    if sender:
        merchant = _clean_merchant(sender.group(1))
        upi_id = sender.group(2).lower()
    elif direction == "credit":
        # Sender named, but with no VPA beside it. Only attempted on a
        # credit: "From:" appears in plenty of debit alerts meaning the
        # account the money LEFT, and reading that as a counterparty is the
        # bug that once booked a card swipe against the sender's own bank.
        named_only = CREDIT_SENDER_NAME_RE.search(text)
        if named_only:
            merchant = _clean_merchant(named_only.group(1))

    # HDFC's "account credited" template with no sender line names no
    # counterparty at all, and the generic patterns below would grab
    # boilerplate prose ("inform you that Rs") instead, since "to your ...
    # account" matches their "to <name>" shape.
    if not merchant and "successfully credited to your" not in text.lower():
        # A parenthesised name beside the VPA beats everything else: it's the
        # only genuinely human-readable merchant the message carries.
        named = VPA_WITH_NAME_RE.search(text)
        if named and not _looks_like_reference(named.group(2)):
            merchant = _clean_merchant(named.group(2))
        if not merchant:
            for pattern in MERCHANT_PATTERNS:
                # finditer, not search: a pattern's FIRST match is often
                # boilerplate that _clean_merchant rejects ("from your HDFC
                # Bank Debit Card" -> "your", a stopword). Only looking at
                # that first match meant one rejected candidate abandoned the
                # whole pattern, so a perfectly good "at FLIPKART PAYMENTS on"
                # later in the same message was never even considered.
                for found in pattern.finditer(text):
                    merchant = _clean_merchant(found.group(1))
                    if merchant:
                        break
                if merchant:
                    break

    # Whether the alert actually identified the other party, decided BEFORE
    # the account-tail fallback below fills in a placeholder. _ingest() uses
    # this to ask "who sent this?" rather than filing an anonymous credit as
    # income forever — which is what happened to a real Rs 10,000.
    has_counterparty = bool(merchant)

    # Nothing named the sender. Rather than "Unknown" — which reads as a
    # parse failure and tells you nothing — say which of your own accounts
    # it landed in, when the alert says that much.
    if not merchant and direction == "credit":
        tail = CREDIT_ACCOUNT_TAIL_RE.search(text)
        if tail:
            bank = re.sub(r"\s+", " ", tail.group(1)).strip()
            merchant = f"Credit to {bank} ...{tail.group(2)}"[:60]

    is_transaction = (
        amount > 0
        and (is_debit or is_credit)
        and not NON_TRANSACTIONAL.search(text)
    )

    return {
        "amount": amount,
        "direction": direction,
        "merchant": merchant or "Unknown",
        "upi_id": upi_id,
        "has_counterparty": has_counterparty,
        "is_transaction": is_transaction,
    }


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

# Only ever anchored to a keyword ("on", "dated", "Date:"), never a bare
# number run — a UPI reference like 659798226430 or an account number would
# otherwise be a rich source of accidental dates.
_DATE_PATTERNS = [
    # "on 19-08-26" / "on 19/08/2026" / "Date: 18-08-26"  -> DD MM YY(YY)
    (re.compile(r"\b(?:on|dated|date:?)\s*(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})\b", re.IGNORECASE), "dmy"),
    # "on 2026-08-15"  -> ISO
    (re.compile(r"\b(?:on|dated|date:?)\s*(\d{4})-(\d{1,2})-(\d{1,2})\b", re.IGNORECASE), "ymd"),
    # "on 09 Aug, 2026" / "on 9 August 2026"
    (re.compile(r"\b(?:on|dated|date:?)\s*(\d{1,2})\s+([A-Za-z]{3,9}),?\s+(\d{4})\b", re.IGNORECASE), "dMy"),
]


def parse_alert_date(text: str) -> tuple[int, int, int] | None:
    """The (year, month, day) the bank says the transaction happened.

    Without this, a transaction's date is whenever it was *ingested*, which
    is fine for a live SMS but wrong for anything backfilled or delayed — a
    fortnight of email alerts imported in one go all landed on the import
    date and drew the spending trend as a flat line then a vertical cliff.

    Returns None rather than guessing; the caller then falls back to now.
    """
    for pattern, order in _DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        try:
            if order == "dmy":
                day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif order == "ymd":
                year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:
                day = int(m.group(1))
                month = _MONTHS.get(m.group(2)[:3].lower(), 0)
                year = int(m.group(3))
            if year < 100:
                year += 2000  # "26" -> 2026; Indian bank alerts use 2-digit years
            if not (1 <= month <= 12 and 1 <= day <= 31 and 2000 <= year <= 2100):
                continue
            from datetime import date
            date(year, month, day)  # rejects 31 Feb and friends
            return year, month, day
        except ValueError:
            continue
    return None


# The bank's own unique id for a transaction. Matching on it is the only
# reliable way to spot the SAME payment arriving twice through different
# channels — an SMS and an email describe one payment in different words, so
# comparing the alert text catches nothing. Two real payments were booked
# twice this way before this existed.
_BANK_REF_RE = re.compile(
    r"(?:reference\s*(?:no\.?|number)?|ref(?:erence)?\s*(?:no\.?)?|rrn|txn\s*id)\s*[:.\-]?\s*(\d{6,20})",
    re.IGNORECASE,
)


def parse_bank_ref(text: str) -> str | None:
    """The transaction reference the bank quotes, if the alert carries one."""
    m = _BANK_REF_RE.search(text)
    return m.group(1) if m else None


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


# Shapes that mean "this is a business", used to question a "that's a person"
# answer before it gets remembered forever.
#
# This exists because of a real mistake in live data: RADDLINS FOOD, a
# bakery, was answered as "a person" and filed under Lending, where it sat in
# the who-owes-you list alongside actual friends. Once remembered, every
# future payment there would have been booked as money lent out rather than
# food — and the answer is never asked again, so nothing would have surfaced
# it.
#
# A hint, never a decision. Getting this backwards (auto-filing a real friend
# as a shop) would silently destroy lending tracking, so the heuristic only
# ever produces a question for the user to answer.
_MERCHANT_VPA_PATTERNS = [
    # Vyapar, Paytm, PhonePe, BharatPe, Razorpay and Google Pay all issue
    # merchant-side UPI ids with recognisable shapes. A person's handle never
    # looks like any of these.
    (re.compile(r"^vyapar\.", re.I), "a Vyapar shop-billing UPI id"),
    (re.compile(r"^(?:paytmqr|paytm-)", re.I), "a Paytm merchant QR id"),
    (re.compile(r"^bharatpe", re.I), "a BharatPe merchant id"),
    (re.compile(r"^q\d{6,}$", re.I), "a PhonePe merchant QR id"),
    (re.compile(r"^\d{6,}$"), "an all-numeric merchant id"),
    (re.compile(r"^(?:rzp|razorpay)", re.I), "a Razorpay merchant id"),
    (re.compile(r"merchant|mrchnt", re.I), "a merchant-flagged UPI id"),
]
_MERCHANT_VPA_DOMAINS = {
    "ptys": "a Paytm merchant handle",
    "okbizaxis": "a Google Pay for Business handle",
    "pz": "a PayZapp merchant handle",
}

# Words that only ever appear in a trading name. Deliberately excludes
# ambiguous ones — "kumar traders" is a shop but "kumar" alone is a person,
# and plenty of Indian personal names would collide with a looser list.
_BUSINESS_WORDS = {
    "store", "stores", "mart", "supermarket", "market", "bakery", "bakers",
    "hotel", "restaurant", "cafe", "canteen", "foods", "food", "sweets",
    "agencies", "agency", "traders", "trading", "enterprises", "enterprise",
    "medicals", "medical", "pharmacy", "pharma", "clinic", "hospital",
    "motors", "automobiles", "petroleum", "fuels", "filling", "petrol",
    "textiles", "garments", "jewellers", "electronics", "hardware",
    "pvt", "ltd", "limited", "llp", "inc", "corp", "company", "industries",
    "technologies", "solutions", "services", "communications", "telecom",
    "provisions", "departmental", "stationery", "salon", "spa", "studio",
    "tiffin", "mess", "caterers", "catering", "juice", "tea", "coffee",
}


def business_hint(label: str, key: str | None) -> str | None:
    """Why this counterparty looks like a business, or None if it doesn't.

    Returns the *reason* rather than a bare boolean: "looks like a shop" is
    not something a user can act on, whereas "vyapar.… is a shop-billing UPI
    id" is checkable against what they know.
    """
    if key and "@" in key:
        local, _, domain = key.partition("@")
        for pattern, reason in _MERCHANT_VPA_PATTERNS:
            if pattern.search(local):
                return reason
        for suffix, reason in _MERCHANT_VPA_DOMAINS.items():
            if domain.lower().endswith(suffix):
                return reason

    words = re.sub(r"[^a-z0-9 ]", " ", (label or "").lower()).split()
    hit = next((w for w in words if w in _BUSINESS_WORDS), None)
    if hit:
        return f'"{hit}" is a trading-name word, not part of a person\'s name'
    return None


def _rule_match(merchant: str, raw_text: str) -> str | None:
    haystack = f"{merchant} {raw_text}".lower()
    for key, category in OBVIOUS_MERCHANTS.items():
        if key in haystack:
            return category
    return None


def _gemini_categorize(merchant: str, raw_text: str) -> dict | None:
    """Same job as the Claude branch below, via Gemini's free tier instead.
    Returns None on any failure (bad key, network, malformed response,
    quota exhausted) so the caller falls through to the review queue rather
    than losing the transaction — this is a nice-to-have automation, not
    something that should ever be able to break ingestion."""
    if not _gemini_key:
        return None

    body = {
        "contents": [{
            "parts": [{
                "text": _CLASSIFY_PROMPT.format(merchant=merchant, raw_text=raw_text),
            }],
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "category": {"type": "STRING", "enum": CATEGORIES},
                    "confident": {"type": "BOOLEAN"},
                    "entity": {"type": "STRING", "enum": ENTITY_KINDS},
                },
                "required": ["category", "confident", "entity"],
            },
            # gemini-3.6-flash thinks by default (confirmed live: 65 thinking
            # tokens spent on "reply with the word ok") — real latency for a
            # trivial classification task that doesn't need it, and wasted
            # tokens against the free-tier daily quota. Off entirely.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    req = urllib.request.Request(
        f"{_GEMINI_ENDPOINT}?key={_gemini_key}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        parsed = json.loads(text)
        category = parsed.get("category", "Other")
        if category not in CATEGORIES:
            category = "Other"
        entity = parsed.get("entity")
        return {
            "category": category,
            "confident": bool(parsed.get("confident", False)),
            # Anything unrecognised falls back to "unclear", never "business" —
            # the safe direction is always "go and ask".
            "entity": entity if entity in ENTITY_KINDS else "unclear",
        }
    except Exception:
        return None


def _azure_categorize(merchant: str, raw_text: str) -> dict | None:
    """Same job again, via an Azure OpenAI deployment. Only reached when
    Gemini itself isn't configured or just failed (e.g. quota) — see
    categorize() below. Returns None on any failure, same contract as
    _gemini_categorize(), for the same reason: never allowed to break
    ingestion, this is strictly a nice-to-have."""
    if not (_azure_key and _azure_endpoint and _azure_deployment):
        return None

    body = {
        "model": _azure_deployment,
        "messages": [{
            "role": "user",
            "content": (
                _CLASSIFY_PROMPT.format(merchant=merchant, raw_text=raw_text)
                + '\n\nReply with a JSON object: '
                  '{"category": string, "confident": bool, "entity": "business"|"person"|"unclear"}'
            ),
        }],
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{_azure_endpoint}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "api-key": _azure_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        parsed = json.loads(data["choices"][0]["message"]["content"])
        category = parsed.get("category", "Other")
        if category not in CATEGORIES:
            category = "Other"
        entity = parsed.get("entity")
        return {
            "category": category,
            "confident": bool(parsed.get("confident", False)),
            "entity": entity if entity in ENTITY_KINDS else "unclear",
        }
    except Exception:
        return None


def categorize(merchant: str, raw_text: str, direction: str = "debit") -> dict:
    """Returns {'category', 'needs_review', 'source', 'entity'}. Cheap path
    first, AI second.

    `source` tells the caller *why* — main.py needs this to decide whether to
    also ask "who is this / merchant, friend, wallet, or my own account?".
    `entity` is what lets it stop asking when the answer is obvious from the
    name: "INIYA MUGIL SOUP" is plainly a shop, "R MANOHARAN" plainly isn't.
    Only a confident "business" skips that question; everything else asks.
    """
    hit = _rule_match(merchant, raw_text)
    if hit:
        # A hardcoded brand match (Swiggy, Amazon...) is a business by
        # definition — that's the entire basis of the OBVIOUS_MERCHANTS list.
        return {"category": hit, "needs_review": False, "source": "rule", "entity": "business"}

    if direction == "credit":
        # Money coming in isn't spending — don't burn an AI call or a review slot.
        return {"category": "Income", "needs_review": False, "source": "income", "entity": "unclear"}

    if client is None:
        # No Claude key — try Azure OpenAI first (reliable, no free-tier
        # quota wall), then Gemini's free tier as backup if that comes back
        # empty (unset, or its own daily quota exhausted — both fall
        # through the same None contract), before giving up to a review slot.
        result = _azure_categorize(merchant, raw_text) or _gemini_categorize(merchant, raw_text)
        if result is None:
            return {"category": "Uncategorized", "needs_review": True, "source": "no_ai", "entity": "unclear"}
        return {
            "category": result["category"],
            "needs_review": not result["confident"],
            "source": "ai_confident" if result["confident"] else "ai_unsure",
            "entity": result.get("entity", "unclear"),
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
                            "entity": {"type": "string", "enum": ENTITY_KINDS},
                        },
                        "required": ["category", "confident", "entity"],
                        "additionalProperties": False,
                    },
                },
            },
            messages=[{
                "role": "user",
                "content": _CLASSIFY_PROMPT.format(merchant=merchant, raw_text=raw_text),
            }],
        )
        text = next(b.text for b in response.content if b.type == "text")
        parsed = json.loads(text)
        category = parsed.get("category", "Other")
        if category not in CATEGORIES:
            category = "Other"
        confident = parsed.get("confident", False)
        entity = parsed.get("entity")
        return {
            "category": category,
            "needs_review": not confident,
            "source": "ai_confident" if confident else "ai_unsure",
            "entity": entity if entity in ENTITY_KINDS else "unclear",
        }
    except Exception:
        # Never let a categorization failure lose the transaction.
        return {"category": "Uncategorized", "needs_review": True, "source": "ai_error", "entity": "unclear"}

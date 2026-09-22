"""Polls Gmail for new bank-alert emails and feeds them through the same
_ingest() pipeline as MacroDroid's SMS forwarding — HDFC (and possibly other
banks later) email every transaction alert for this account rather than
texting it, so SMS-only automation misses them entirely.

Raw HTTP via urllib, same style as receipt_scan.py — no Google client
library dependency for a handful of REST calls.

OAuth flow (one-time, done by hand — see main.py's /gmail/auth/* routes):
  1. Visit /gmail/auth/start?token=<AUTH_TOKEN> in a browser, sign in with
     the Gmail account to poll, approve read-only access.
  2. Google redirects to /gmail/auth/callback, which exchanges the
     authorization code for a refresh token and stores it in the GmailAuth
     table (see models.py).
Refresh tokens don't expire from age, only from revocation, so this needs
no re-auth once done. /gmail/poll then trades it for a short-lived access
token on every run — triggered on a schedule by
.github/workflows/gmail-poll.yml, the same pattern keep-alive.yml uses to
keep Render's free tier warm.
"""

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "").strip()

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

# Bank sender addresses to poll — add more here as other banks/accounts come
# into use, same "add to the list, don't widen a regex" convention as
# merchants.py.
BANK_SENDERS = ["alerts@hdfcbank.bank.in"]

# First-ever poll has no watermark to work from — look back this far once,
# then narrow to "since last poll" for every run after.
INITIAL_LOOKBACK_DAYS = 2

# How much backlog ONE poll will attempt, in days, and how many full message
# bodies it will fetch in that attempt. Both exist for the same reason: a
# poll that had gone unrun for 19 days (its Gmail OAuth token had silently
# expired) tried to fetch the ENTIRE backlog in one call — dozens of full
# emails, fetched one at a time over the network — on a 512MB free-tier
# instance, and it kept getting killed partway through before ever
# committing. Worse: the watermark only ever advanced on full success, so
# every retry re-attempted the exact same impossible backlog and could
# never make progress — a genuine "stuck forever" bug, not just slowness.
#
# Capping the WINDOW (not just the result count) is what makes catch-up
# actually terminate: each poll only ever asks Gmail for a few days' worth
# at once, so the watermark can advance in small, safe, verified steps
# even when there are weeks of backlog — a large gap just takes several
# scheduled polls to fully close instead of one that can never succeed.
MAX_CATCHUP_DAYS_PER_POLL = 3
MAX_MESSAGES_PER_POLL = 25


class GmailPollError(Exception):
    pass


def configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET and REDIRECT_URI)


def auth_url() -> str:
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",  # forces a refresh_token even on a repeat consent
    }
    return f"{AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"


def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise GmailPollError(f"Google token request failed ({e.code}): {detail}") from e
    except urllib.error.URLError as e:
        raise GmailPollError(f"Couldn't reach Google: {e.reason}") from e


def exchange_code(code: str) -> dict:
    """One-time: authorization code -> {access_token, refresh_token, ...}."""
    return _post_form(TOKEN_ENDPOINT, {
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    })


def _access_token(refresh_token: str) -> str:
    data = _post_form(TOKEN_ENDPOINT, {
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "refresh_token",
    })
    token = data.get("access_token")
    if not token:
        raise GmailPollError(f"No access_token in refresh response: {data}")
    return token


def _get(url: str, access_token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise GmailPollError(f"Gmail API request failed ({e.code}): {detail}") from e
    except urllib.error.URLError as e:
        raise GmailPollError(f"Couldn't reach Gmail: {e.reason}") from e


def _html_to_text(html: str) -> str:
    """Crude but effective: strip whatever isn't visible text.

    Real bank-alert emails (HDFC's included) are often dense, image- and
    table-heavy marketing templates with no text/plain part at all — just
    regexing out `<...>` tags leaves entire <style> block CSS and hundreds
    of empty <td></td> table cells behind as "text". That padding easily
    pushes the actual message past any reasonable truncation length before
    parse_sms() ever sees it, which is exactly what silently broke the
    first live poll: real transaction alerts came back "not a transaction"
    because the amount was buried a few thousand characters in, past
    fetch_new_alerts()'s 2000-char cutoff. Stripping <style>/<script>/<head>
    *content* (not just their tags) and collapsing the remaining whitespace
    keeps the real message near the front where it belongs.
    """
    html = re.sub(r"(?is)<(style|script|head)\b.*?</\1>", " ", html)
    html = re.sub(r"<[^>]+>", " ", html)
    html = html.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", html).strip()


def _extract_plain_text(payload: dict) -> str:
    """Gmail bodies are base64url in a nested MIME tree; walk it for the
    first text/plain part, falling back to text/html reduced to visible text."""

    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if mime == "text/plain" and data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        for sub in part.get("parts", []) or []:
            found = walk(sub)
            if found:
                return found
        if mime == "text/html" and data:
            html = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            return _html_to_text(html)
        return None

    return walk(payload) or ""


def fetch_new_alerts(refresh_token: str, since: datetime | None) -> tuple[list[str], datetime]:
    """Returns (plain-text bodies of matching emails, the watermark to save).

    The window is capped at MAX_CATCHUP_DAYS_PER_POLL — see its comment for
    why an uncapped "everything since last time" fetch is a real bug, not
    just a slow path. The caller MUST persist the returned watermark exactly
    (not `datetime.utcnow()`): it marks precisely how far this call actually
    got, which may be well short of "now" when there's a lot of backlog left.
    """
    access_token = _access_token(refresh_token)
    now = datetime.utcnow()

    # This whole app stores naive UTC everywhere (see timeutil.utc_now_naive,
    # used to WRITE this exact column) - but what comes back on the next READ
    # from Postgres depends on the driver/dialect, and can be timezone-aware
    # even for a value that was written naive. The old code never noticed,
    # because it only ever called .timestamp() on `since`, which silently
    # accepts either. The new min()/comparison logic below does not: mixing
    # an aware `since` with naive `now` throws "can't compare offset-naive
    # and offset-aware datetimes" - confirmed live, a real 500 on the very
    # first production run of this fix. Normalising here, once, is cheaper
    # and more robust than auditing every downstream comparison.
    if since is not None and since.tzinfo is not None:
        since = since.astimezone(timezone.utc).replace(tzinfo=None)

    cutoff = since or (now - timedelta(days=INITIAL_LOOKBACK_DAYS))
    window_end = min(cutoff + timedelta(days=MAX_CATCHUP_DAYS_PER_POLL), now)

    sender_q = " OR ".join(f"from:{s}" for s in BANK_SENDERS)
    # `before:` needs a day of slack the same way `after:` does (both are
    # date-only in Gmail's search grammar) — the precise internalDate check
    # below is what actually enforces the exact boundary either side.
    query = (
        f"({sender_q}) after:{cutoff.strftime('%Y/%m/%d')} "
        f"before:{(window_end + timedelta(days=1)).strftime('%Y/%m/%d')}"
    )

    list_url = f"{GMAIL_API}/messages?{urllib.parse.urlencode({'q': query, 'maxResults': MAX_MESSAGES_PER_POLL})}"
    listing = _get(list_url, access_token)

    since_ms = int(since.timestamp() * 1000) if since else None
    window_end_ms = int(window_end.timestamp() * 1000)

    texts = []
    for m in listing.get("messages", []):
        msg = _get(f"{GMAIL_API}/messages/{m['id']}?format=full", access_token)
        # Both bounds are date-only in the query above; enforce the real,
        # precise window here so nothing on either edge is double-counted or
        # silently skipped between one poll's window and the next's.
        internal_ms = int(msg.get("internalDate", "0"))
        if since_ms is not None and internal_ms <= since_ms:
            continue
        if internal_ms > window_end_ms:
            continue
        text = _extract_plain_text(msg.get("payload", {}))
        if text:
            texts.append(text[:2000])
    return texts, window_end

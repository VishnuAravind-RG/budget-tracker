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
from datetime import datetime, timedelta

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


def _extract_plain_text(payload: dict) -> str:
    """Gmail bodies are base64url in a nested MIME tree; walk it for the
    first text/plain part, falling back to text/html stripped of tags."""

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
            return re.sub(r"<[^>]+>", " ", html)
        return None

    return walk(payload) or ""


def fetch_new_alerts(refresh_token: str, since: datetime | None) -> list[str]:
    """Returns the plain-text body of every bank-alert email newer than
    `since` (or the last INITIAL_LOOKBACK_DAYS days, on the very first poll).
    """
    access_token = _access_token(refresh_token)

    cutoff = since or (datetime.utcnow() - timedelta(days=INITIAL_LOOKBACK_DAYS))
    sender_q = " OR ".join(f"from:{s}" for s in BANK_SENDERS)
    query = f"({sender_q}) after:{cutoff.strftime('%Y/%m/%d')}"

    list_url = f"{GMAIL_API}/messages?{urllib.parse.urlencode({'q': query, 'maxResults': 50})}"
    listing = _get(list_url, access_token)

    texts = []
    for m in listing.get("messages", []):
        msg = _get(f"{GMAIL_API}/messages/{m['id']}?format=full", access_token)
        # `after:` is date-only (no time-of-day), so it can return messages
        # from earlier the same day as `since` — re-filter on the real
        # internalDate to skip those. _ingest()'s own dedupe-by-raw-text
        # would catch them anyway; this just avoids the redundant work.
        internal_ms = int(msg.get("internalDate", "0"))
        if since and internal_ms <= int(since.timestamp() * 1000):
            continue
        text = _extract_plain_text(msg.get("payload", {}))
        if text:
            texts.append(text[:2000])
    return texts

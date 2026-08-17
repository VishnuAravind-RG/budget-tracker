"""Timestamps are stored as naive UTC; month windows are computed in your local
timezone so a late-night spend doesn't land in next month's budget."""

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Your wall-clock timezone. Budget months are cut on these boundaries.
LOCAL_TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Kolkata"))


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ)


def month_range_utc(year: int, month: int) -> tuple[datetime, datetime]:
    """Naive-UTC [start, end) covering the given local-calendar month."""
    start_local = datetime(year, month, 1, tzinfo=LOCAL_TZ)
    if month == 12:
        end_local = datetime(year + 1, 1, 1, tzinfo=LOCAL_TZ)
    else:
        end_local = datetime(year, month + 1, 1, tzinfo=LOCAL_TZ)
    to_utc = lambda dt: dt.astimezone(timezone.utc).replace(tzinfo=None)  # noqa: E731
    return to_utc(start_local), to_utc(end_local)


def local_day_key(dt_utc_naive: datetime) -> str:
    """Which local calendar day a stored (naive UTC) timestamp falls on."""
    aware = dt_utc_naive.replace(tzinfo=timezone.utc)
    return aware.astimezone(LOCAL_TZ).strftime("%Y-%m-%d")


def month_anchor_utc(year: int, month: int) -> datetime:
    """Naive-UTC timestamp for noon local time on the 1st of the given month —
    for booking a historical entry that only has a month, not a real date.
    Noon (not midnight) keeps it safely inside the same local calendar day
    after the UTC conversion, regardless of DST or offset."""
    local_noon = datetime(year, month, 1, 12, 0, tzinfo=LOCAL_TZ)
    return local_noon.astimezone(timezone.utc).replace(tzinfo=None)


def days_in_month(year: int, month: int) -> int:
    first = datetime(year, month, 1)
    nxt = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return (nxt - first - timedelta(seconds=1)).days + 1

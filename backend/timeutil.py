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


def local_date_to_utc(year: int, month: int, day: int) -> datetime:
    """Naive-UTC timestamp for noon local time on a specific local date.

    Bank alerts carry a date but no useful time, and the date they mean is
    the local one. Noon (not midnight) keeps it inside the same local
    calendar day after the UTC conversion, so a transaction dated the 5th
    can't drift onto the 4th and land in the wrong day — or, on the 1st of a
    month, the wrong month's budget.
    """
    local_noon = datetime(year, month, day, 12, 0, tzinfo=LOCAL_TZ)
    return local_noon.astimezone(timezone.utc).replace(tzinfo=None)


def period_range_utc(period: str, offset: int = 0) -> tuple[datetime, datetime]:
    """Naive-UTC [start, end) for a local day / week / month, `offset` periods
    back from the current one (0 = current, 1 = the one before).

    All boundaries are computed in LOCAL time and only then converted, which
    is the whole point: a spend at 1am local belongs to that local day, not to
    the previous one that UTC would put it in. Weeks start Monday.
    """
    now = datetime.now(LOCAL_TZ)

    if period == "day":
        start_local = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=offset)
        end_local = start_local + timedelta(days=1)
    elif period == "week":
        monday = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
        start_local = monday - timedelta(weeks=offset)
        end_local = start_local + timedelta(weeks=1)
    elif period == "month":
        month = now.month - offset
        year = now.year
        while month < 1:
            month += 12
            year -= 1
        start_local = datetime(year, month, 1, tzinfo=LOCAL_TZ)
        end_local = (
            datetime(year + 1, 1, 1, tzinfo=LOCAL_TZ) if month == 12
            else datetime(year, month + 1, 1, tzinfo=LOCAL_TZ)
        )
    else:
        raise ValueError("period must be day, week or month")

    to_utc = lambda dt: dt.astimezone(timezone.utc).replace(tzinfo=None)  # noqa: E731
    return to_utc(start_local), to_utc(end_local)


def period_label(period: str, offset: int = 0) -> str:
    """Human label for the window `period_range_utc` returns."""
    now = datetime.now(LOCAL_TZ)
    if period == "day":
        if offset == 0:
            return "Today"
        if offset == 1:
            return "Yesterday"
        return (now - timedelta(days=offset)).strftime("%-d %b" if os.name != "nt" else "%d %b")
    if period == "week":
        if offset == 0:
            return "This week"
        if offset == 1:
            return "Last week"
        return f"{offset} weeks ago"
    if offset == 0:
        return "This month"
    if offset == 1:
        return "Last month"
    month = now.month - offset
    year = now.year
    while month < 1:
        month += 12
        year -= 1
    return datetime(year, month, 1).strftime("%B %Y")


def days_in_month(year: int, month: int) -> int:
    first = datetime(year, month, 1)
    nxt = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return (nxt - first - timedelta(seconds=1)).days + 1

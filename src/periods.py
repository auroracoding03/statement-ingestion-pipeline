"""Inclusive posted-date windows for Overview, Categories, and Transactions.

Presets are resolved against an as-of month, defaulting to the latest month in
the ledger rather than the wall clock. A ledger that ends in July should not
open an empty August.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

PRESETS = ("month", "prev_month", "t3m", "t12m", "ytd", "custom")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


@dataclass(frozen=True)
class Period:
    preset: str
    month: str | None
    since: str
    until: str
    prior_since: str | None
    prior_until: str | None
    label: str


def month_keys(ledger: pd.DataFrame) -> list[str]:
    if ledger.empty or "posted_date" not in ledger.columns:
        return []
    keys = pd.to_datetime(ledger["posted_date"], errors="coerce").dt.strftime("%Y-%m")
    return sorted({key for key in keys.dropna() if key != "NaT"})


def parse_month(value: str | None) -> str | None:
    if not value:
        return None
    match = _MONTH_RE.fullmatch(value.strip())
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    if month < 1 or month > 12:
        return None
    return f"{year:04d}-{month:02d}"


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    match = _DATE_RE.fullmatch(value.strip())
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def shift_month(month: str, delta: int) -> str:
    year, month_n = (int(part) for part in month.split("-"))
    total = year * 12 + (month_n - 1) + delta
    return f"{total // 12:04d}-{(total % 12) + 1:02d}"


def month_start(month: str) -> date:
    year, month_n = (int(part) for part in month.split("-"))
    return date(year, month_n, 1)


def month_end(month: str) -> date:
    year, month_n = (int(part) for part in month.split("-"))
    return date(year, month_n, calendar.monthrange(year, month_n)[1])


def _iso(value: date) -> str:
    return value.isoformat()


def _span(since_month: str, until_month: str) -> tuple[str, str]:
    return _iso(month_start(since_month)), _iso(month_end(until_month))


def _prior_months(since_month: str, until_month: str) -> tuple[str, str]:
    """Equal-length calendar-month window immediately before since_month."""
    start_y, start_m = (int(part) for part in since_month.split("-"))
    end_y, end_m = (int(part) for part in until_month.split("-"))
    length = (end_y * 12 + end_m) - (start_y * 12 + start_m) + 1
    prior_until = shift_month(since_month, -1)
    prior_since = shift_month(prior_until, -(length - 1))
    return prior_since, prior_until


def resolve_period(
    *,
    preset: str = "month",
    month: str | None = None,
    since: str | None = None,
    until: str | None = None,
    months: list[str] | None = None,
) -> Period:
    if preset not in PRESETS:
        raise ValueError(f"Unknown period preset {preset!r}")

    known = months or []
    as_of = parse_month(month) or (known[-1] if known else None)

    if preset == "custom":
        start = parse_iso_date(since)
        end = parse_iso_date(until)
        if start is None or end is None:
            raise ValueError("custom periods require since and until as YYYY-MM-DD")
        if end < start:
            raise ValueError("until must be on or after since")
        length = (end - start).days + 1
        prior_end = start - timedelta(days=1)
        prior_start = prior_end - timedelta(days=length - 1)
        return Period(
            preset="custom",
            month=as_of,
            since=_iso(start),
            until=_iso(end),
            prior_since=_iso(prior_start),
            prior_until=_iso(prior_end),
            label=f"{_iso(start)} → {_iso(end)}",
        )

    if as_of is None:
        raise ValueError("A month is required when the ledger has no posted dates")

    if preset == "month":
        window_since, window_until = as_of, as_of
        label = as_of
    elif preset == "prev_month":
        window_since = window_until = shift_month(as_of, -1)
        label = window_since
    elif preset == "t3m":
        window_since, window_until = shift_month(as_of, -2), as_of
        label = f"{window_since} → {window_until}"
    elif preset == "t12m":
        window_since, window_until = shift_month(as_of, -11), as_of
        label = f"{window_since} → {window_until}"
    else:  # ytd: compare to the same months last year, not the months before January
        year = as_of.split("-")[0]
        prior_year = str(int(year) - 1)
        since_s, until_s = _span(f"{year}-01", as_of)
        prior_since_s, prior_until_s = _span(f"{prior_year}-01", f"{prior_year}-{as_of[5:]}")
        return Period(
            preset="ytd",
            month=as_of,
            since=since_s,
            until=until_s,
            prior_since=prior_since_s,
            prior_until=prior_until_s,
            label=f"{year} YTD through {as_of[5:]}",
        )

    since_s, until_s = _span(window_since, window_until)
    prior_since_m, prior_until_m = _prior_months(window_since, window_until)
    prior_since_s, prior_until_s = _span(prior_since_m, prior_until_m)
    return Period(
        preset=preset,
        month=as_of,
        since=since_s,
        until=until_s,
        prior_since=prior_since_s,
        prior_until=prior_until_s,
        label=label,
    )


def has_prior_history(period: Period, months: list[str]) -> bool:
    if not months or not period.prior_until:
        return False
    return period.prior_until[:7] >= months[0]


def filter_posted(frame: pd.DataFrame, since: str | None = None, until: str | None = None) -> pd.DataFrame:
    """Inclusive filter on posted_date. ``until`` includes the whole calendar day."""
    if frame.empty or "posted_date" not in frame.columns:
        return frame
    if not since and not until:
        return frame
    posted = pd.to_datetime(frame["posted_date"], errors="coerce")
    mask = posted.notna()
    if since:
        start = parse_iso_date(since)
        if start is None:
            raise ValueError("since must be YYYY-MM-DD")
        mask &= posted >= pd.Timestamp(start)
    if until:
        end = parse_iso_date(until)
        if end is None:
            raise ValueError("until must be YYYY-MM-DD")
        mask &= posted.dt.normalize() <= pd.Timestamp(end)
    return frame.loc[mask].copy()

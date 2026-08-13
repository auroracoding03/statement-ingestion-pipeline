"""Inclusive period presets for overview and transaction filters."""

from datetime import date

import pandas as pd

from src.periods import (
    filter_posted,
    has_prior_history,
    month_keys,
    resolve_period,
    shift_month,
)


def test_shift_month_crosses_year_boundary():
    assert shift_month("2026-01", -1) == "2025-12"
    assert shift_month("2025-08", 11) == "2026-07"


def test_month_preset_matches_calendar_month_and_prior():
    period = resolve_period(preset="month", month="2026-07", months=["2026-06", "2026-07"])
    assert period.since == "2026-07-01"
    assert period.until == "2026-07-31"
    assert period.prior_since == "2026-06-01"
    assert period.prior_until == "2026-06-30"
    assert period.label == "2026-07"
    assert period.month == "2026-07"


def test_t12m_and_prior_window():
    period = resolve_period(preset="t12m", month="2026-07", months=["2024-01", "2026-07"])
    assert period.since == "2025-08-01"
    assert period.until == "2026-07-31"
    assert period.prior_since == "2024-08-01"
    assert period.prior_until == "2025-07-31"
    assert period.label == "2025-08 → 2026-07"


def test_t3m_window():
    period = resolve_period(preset="t3m", month="2026-07", months=["2026-07"])
    assert period.since == "2026-05-01"
    assert period.until == "2026-07-31"
    assert period.prior_since == "2026-02-01"
    assert period.prior_until == "2026-04-30"


def test_ytd_through_as_of_month():
    period = resolve_period(preset="ytd", month="2026-07", months=["2026-07"])
    assert period.since == "2026-01-01"
    assert period.until == "2026-07-31"
    assert period.prior_since == "2025-01-01"
    assert period.prior_until == "2025-07-31"


def test_prev_month_uses_as_of():
    period = resolve_period(preset="prev_month", month="2026-07", months=["2026-07"])
    assert period.since == "2026-06-01"
    assert period.until == "2026-06-30"
    assert period.prior_since == "2026-05-01"
    assert period.prior_until == "2026-05-31"


def test_custom_equal_length_prior_is_inclusive():
    period = resolve_period(
        preset="custom",
        since="2026-01-01",
        until="2026-03-31",
        months=["2026-03"],
    )
    assert period.since == "2026-01-01"
    assert period.until == "2026-03-31"
    assert period.prior_until == "2025-12-31"
    assert (date.fromisoformat(period.until) - date.fromisoformat(period.since)).days == (
        date.fromisoformat(period.prior_until) - date.fromisoformat(period.prior_since)
    ).days


def test_custom_rejects_inverted_range():
    try:
        resolve_period(preset="custom", since="2026-03-01", until="2026-01-01")
    except ValueError as exc:
        assert "until" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_defaults_as_of_to_latest_ledger_month():
    period = resolve_period(preset="month", months=["2025-12", "2026-01"])
    assert period.month == "2026-01"
    assert period.since == "2026-01-01"


def test_has_prior_history_false_when_prior_ends_before_ledger():
    period = resolve_period(preset="t12m", month="2026-01", months=["2026-01"])
    assert has_prior_history(period, ["2026-01"]) is False
    assert has_prior_history(period, ["2024-01", "2026-01"]) is True


def test_filter_posted_is_inclusive_on_until():
    frame = pd.DataFrame(
        {
            "posted_date": ["2026-01-01", "2026-01-10", "2026-01-31", "2026-02-01"],
            "amount": [1, 2, 3, 4],
        }
    )
    got = filter_posted(frame, since="2026-01-10", until="2026-01-31")
    assert list(got["amount"]) == [2, 3]


def test_month_keys_sorted_unique():
    frame = pd.DataFrame({"posted_date": ["2026-07-02", "2026-01-10", "2026-07-15"]})
    assert month_keys(frame) == ["2026-01", "2026-07"]

"""Shared parser helper coverage."""

from __future__ import annotations

from datetime import date

from config.parsers.base import parse_month_day, resolve_cycle_date


def test_parse_month_day_does_not_use_year_1900():
    assert parse_month_day("02/29") == (2, 29)
    assert parse_month_day("Feb 29") == (2, 29)
    assert parse_month_day("04/31") == (4, 31)
    assert parse_month_day("13/01") is None


def test_resolve_cycle_date_prefers_exact_period_match():
    assert resolve_cycle_date(11, 20, date(2024, 11, 8), date(2024, 12, 8)) == date(2024, 11, 20)


def test_resolve_cycle_date_allows_one_day_grace():
    assert resolve_cycle_date(11, 7, date(2024, 11, 8), date(2024, 12, 8)) == date(2024, 11, 7)
    assert resolve_cycle_date(12, 9, date(2024, 11, 8), date(2024, 12, 8)) == date(2024, 12, 9)


def test_resolve_cycle_date_keeps_leap_day_in_leap_year_cycle():
    assert resolve_cycle_date(2, 29, date(2024, 2, 25), date(2024, 3, 24)) == date(2024, 2, 29)


def test_resolve_cycle_date_skips_leap_day_outside_non_leap_cycle():
    assert resolve_cycle_date(2, 29, date(2023, 2, 25), date(2023, 3, 24)) is None


def test_resolve_cycle_date_skips_far_outside_period():
    assert resolve_cycle_date(10, 1, date(2024, 11, 8), date(2024, 12, 8)) is None

"""Shared parser helper coverage."""

from __future__ import annotations

from datetime import date

from config.parsers.base import resolve_cycle_date


def test_resolve_cycle_date_prefers_exact_period_match():
    assert resolve_cycle_date(11, 20, date(2024, 11, 8), date(2024, 12, 8)) == date(2024, 11, 20)


def test_resolve_cycle_date_allows_one_day_grace():
    assert resolve_cycle_date(11, 7, date(2024, 11, 8), date(2024, 12, 8)) == date(2024, 11, 7)
    assert resolve_cycle_date(12, 9, date(2024, 11, 8), date(2024, 12, 8)) == date(2024, 12, 9)


def test_resolve_cycle_date_skips_far_outside_period():
    assert resolve_cycle_date(10, 1, date(2024, 11, 8), date(2024, 12, 8)) is None

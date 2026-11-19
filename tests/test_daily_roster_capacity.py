"""Roster capacity must never displace held or pending symbols."""

import pytest

from tradingagents.paper.autopilot.daily_roster import choose_names

CURRENT = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "COST", "JPM"]


def test_full_active_roster_survives_fresh_first_candidate():
    assert choose_names(CURRENT, set(CURRENT), ["TSLA", "AAPL"]) == CURRENT


def test_seven_active_names_leave_one_slot_for_ranked_candidates():
    active = set(CURRENT[:7])
    selected = choose_names(CURRENT, active, ["TSLA", "UNH", "JPM"])
    assert selected == CURRENT[:7] + ["TSLA"]
    assert active.issubset(selected)


def test_no_active_exposure_retains_ranked_order_without_padding():
    assert choose_names(CURRENT, set(), ["TSLA", "UNH"]) == ["TSLA", "UNH"]
    assert choose_names(CURRENT, set(), None) == CURRENT


def test_more_than_eight_active_symbols_still_fail_closed():
    with pytest.raises(ValueError, match="distinct canonical stock symbols"):
        choose_names(CURRENT + ["TSLA"], set(CURRENT + ["TSLA"]), ["UNH"])


def test_active_symbol_outside_full_current_roster_still_fails_closed():
    with pytest.raises(ValueError, match="missing from account watchlist"):
        choose_names(CURRENT, set(CURRENT + ["TSLA"]), ["UNH"])

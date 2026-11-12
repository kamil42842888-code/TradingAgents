"""Focused integration with the REAL TradingAgents V3.1 state and risk engine.

All inputs are synthetic and dated; these tests never touch the default
portfolio, observation home, AI vendor, broker, network or Windows scheduler.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tradingagents.paper.autopilot import paper_bridge
from tradingagents.paper.autopilot.state import create_new, read

SESSION = "2026-10-30"
NY = ZoneInfo("America/New_York")
NAMES = ["MU", "RNG", "XNCR", "GEO", "TVTX", "BWIN", "EQNR", "RGA"]


def local(hour, minute=10):
    return datetime(2026, 10, 30, hour, minute, tzinfo=NY)


def utc(local_time):
    return local_time.astimezone(UTC).isoformat()


def make_calendar(phase):
    captured = local(9, 10) if phase == "preopen" else local(17, 10)
    rows = {
        name: {
            "asof_date": SESSION,
            "next_earnings_date": "2026-12-10",
            "last_earnings_date": "2026-09-15",
            "source": "YAHOO+NASDAQ_CONSENSUS_SECONDARY",
            "verified": True,
            "observed_at_utc": utc(captured),
        }
        for name in NAMES
    }
    return {
        "session": SESSION,
        "phase": phase,
        "observed_utc": utc(captured),
        "source_policy": "TWO_SECONDARY_FEEDS_MUST_AGREE_OR_BLOCK",
        "earnings": rows,
    }


@pytest.fixture
def source_and_account(tmp_path):
    ledger = tmp_path / "paper" / "v3_autopilot" / "portfolio.json"
    create_new(ledger, "2026-10-22", ["AAPL", "MSFT", "NVDA"])
    observer = tmp_path / "observations" / "koral_v3"
    target = observer / "calendar" / SESSION
    target.mkdir(parents=True)
    for phase in ("preopen", "preclose"):
        (target / (phase + ".json")).write_text(json.dumps(make_calendar(phase)))
    folder = observer / "market"
    folder.mkdir(parents=True)
    bars = {name: {"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0} for name in NAMES}
    bars["SPY"] = {"open": 600.0, "high": 602.0, "low": 599.0, "close": 601.0}
    market = {
        "schema": "koral-observation-v1",
        "mode": "OBSERVE_ONLY",
        "session": SESSION,
        "phase": "close",
        "capture_started_utc": utc(local(17, 45)),
        "capture_completed_utc": utc(local(17, 49)),
        "source": "YAHOO_SECONDARY_NOT_EXCHANGE_CERTIFIED",
        "ohlc_usd": bars,
        "fx_gbp_per_usd_open": 0.75,
        "fx_gbp_per_usd_close": 0.75,
        "corporate_actions": "QUERIED_NO_UNMODELLED_EVENTS_REPORTED",
        "orders": [],
    }
    (folder / (SESSION + ".json")).write_text(json.dumps(market))
    return ledger, observer


def test_read_only_preflight_never_changes_existing_paper(source_and_account):
    ledger, observer = source_and_account
    before = ledger.read_bytes()
    summary = paper_bridge.preview(ledger, observer, SESSION, NAMES, local(18, 15))
    assert summary["roster_before"] == ["AAPL", "MSFT", "NVDA"]
    assert summary["roster_after"] == NAMES
    assert summary["eligible_ai_count"] == 8
    assert summary["paper_orders"] == summary["models_called"] == 0
    assert ledger.read_bytes() == before


def test_one_time_paper_baseline_binds_real_book_and_preserves_audit(source_and_account):
    ledger, observer = source_and_account
    old = read(ledger)
    before = ledger.read_bytes()
    result = paper_bridge.bootstrap(ledger, observer, SESSION, NAMES, local(18, 15))
    assert result["status"] == "FORWARD_PAPER_BASELINE_BOUND"
    state = read(ledger)
    assert state.created_utc == old.created_utc
    assert state.audit[0] == old.audit[0]
    assert state.watchlist == NAMES
    assert state.auto_paper_enabled is False
    assert state.portfolio.book.last_session == SESSION
    assert len(state.portfolio.book.equity_history) == 1
    assert state.portfolio.book.equity() == 10000
    assert state.portfolio.book.cash_gbp == 10000
    assert state.portfolio.book.positions == {}
    assert state.portfolio.book.orders == []
    assert state.portfolio.intents == {}
    assert state.ai_receipts == {}
    assert any(e["kind"] == "OBSERVER_SOURCE_BOUND" for e in state.audit)
    assert any(e["kind"] == "FORWARD_BASELINE_ONLY" for e in state.audit)
    assert Path(result["backup"]).read_bytes() == before
    assert not any(e["kind"] == "V3_POLICY_ACCEPTED" for e in state.audit)


@pytest.mark.parametrize(
    "changed_file, expected_error",
    [
        ("observer_sources/2026-10-30/market.json", "Bound observer evidence changed or missing"),
        ("observer_sources/2026-10-30/preopen.json", "Bound observer evidence changed or missing"),
        ("observer_sources/2026-10-30/preclose.json", "Bound observer evidence changed or missing"),
        ("calendar/2026-10-30/preopen.json", "Bound paper earnings calendar changed or missing"),
        ("calendar/2026-10-30/preclose.json", "Bound paper earnings calendar changed or missing"),
    ],
)
def test_imported_source_tampering_is_rejected_on_every_future_ledger_read(
    source_and_account, changed_file, expected_error
):
    ledger, observer = source_and_account
    paper_bridge.bootstrap(ledger, observer, SESSION, NAMES, local(18, 15))
    evidence = ledger.parent / changed_file
    evidence.write_bytes(evidence.read_bytes() + b"\n")
    with pytest.raises(ValueError, match=expected_error):
        read(ledger)


def test_invalid_preopen_fails_without_changing_account(source_and_account):
    ledger, observer = source_and_account
    original = ledger.read_bytes()
    path = observer / "calendar" / SESSION / "preopen.json"
    value = json.loads(path.read_text())
    value["observed_utc"] = utc(local(18, 0))
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="outside its original NY window"):
        paper_bridge.bootstrap(ledger, observer, SESSION, NAMES, local(18, 15))
    assert ledger.read_bytes() == original
    assert not (ledger.parent / "market_evidence").exists()


def test_historical_date_never_bootstraps_a_forward_paper_trade(source_and_account):
    ledger, observer = source_and_account
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match="SAME-SESSION"):
        paper_bridge.bootstrap(ledger, observer, SESSION, NAMES, local(18, 15) + timedelta(days=1))
    assert ledger.read_bytes() == before


def test_disabled_account_can_only_bootstrap_once(source_and_account):
    ledger, observer = source_and_account
    paper_bridge.bootstrap(ledger, observer, SESSION, NAMES, local(18, 15))
    original = ledger.read_bytes()
    with pytest.raises(ValueError, match="untouched flat baseline"):
        paper_bridge.bootstrap(ledger, observer, SESSION, NAMES, local(18, 15))
    assert ledger.read_bytes() == original


def test_existing_account_with_exposure_is_not_overwritten(source_and_account):
    ledger, observer = source_and_account
    data = json.loads(ledger.read_text())
    data["auto_paper_enabled"] = True
    # Deliberately corrupting audit would make read() reject even earlier:
    # any failure here is a correct fail-closed outcome.
    ledger.write_text(json.dumps(data))
    before = ledger.read_bytes()
    with pytest.raises(ValueError):
        paper_bridge.bootstrap(ledger, observer, SESSION, NAMES, local(18, 15))
    assert ledger.read_bytes() == before

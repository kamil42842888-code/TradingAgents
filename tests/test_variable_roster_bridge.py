"""New contract only: 4–8 genuine roster names, no artificial padding, safe baseline.

All I/O is synthetic under tmp_path. No real account, broker, AI, or vendor calls.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import stage_next_observer_roster as roster
from tests.test_paper_bridge_integration import (
    SESSION,
    local,
    source_and_account as source_and_account,
)
from tradingagents.paper.autopilot import paper_bridge
from tradingagents.paper.autopilot.state import read

FIVE = ["TWST", "CRWD", "MRVL", "AVAH", "ITEK"]
SOURCE = "2026-10-30"
TARGET = "2026-11-02"
NY = ZoneInfo("America/New_York")


@pytest.fixture
def five_sources(source_and_account):
    ledger, observer = source_and_account
    for phase in ("preopen", "preclose"):
        path = observer / "calendar" / SESSION / (phase + ".json")
        data = json.loads(path.read_text())
        original = next(iter(data["earnings"].values()))
        data["earnings"] = dict.fromkeys(FIVE, original)
        path.write_text(json.dumps(data))
    market = observer / "market" / (SESSION + ".json")
    data = json.loads(market.read_text())
    candle = next(v for k, v in data["ohlc_usd"].items() if k != "SPY")
    data["ohlc_usd"] = dict.fromkeys(FIVE, candle) | {"SPY": data["ohlc_usd"]["SPY"]}
    market.write_text(json.dumps(data))
    return ledger, observer


def test_five_candidate_bridge_can_preserve_real_original_ledger(five_sources):
    ledger, observer = five_sources
    before = ledger.read_bytes()
    p = paper_bridge.preview(ledger, observer, SESSION, FIVE, local(18, 15))
    assert p["roster_after"] == FIVE and p["paper_orders"] == 0
    assert ledger.read_bytes() == before
    result = paper_bridge.bootstrap(ledger, observer, SESSION, FIVE, local(18, 15))
    state = read(ledger)
    assert result["status"] == "FORWARD_PAPER_BASELINE_BOUND"
    assert state.watchlist == FIVE
    assert state.portfolio.book.last_session == SESSION
    assert state.portfolio.book.equity() == 10000
    assert state.auto_paper_enabled is False
    assert state.portfolio.book.orders == [] and state.portfolio.book.positions == {}
    assert Path(result["backup"]).read_bytes() == before


def test_three_is_not_padded_to_qualify(five_sources):
    ledger, observer = five_sources
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match="4-8"):
        paper_bridge.preview(ledger, observer, SESSION, FIVE[:3], local(18, 15))
    assert ledger.read_bytes() == before


def test_five_source_tampering_still_vetoes_ledger_reads(five_sources):
    ledger, observer = five_sources
    paper_bridge.bootstrap(ledger, observer, SESSION, FIVE, local(18, 15))
    saved = ledger.parent / "calendar" / SESSION / "preclose.json"
    saved.write_bytes(saved.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Bound paper earnings calendar changed"):
        read(ledger)


def test_retroactive_five_stock_baseline_is_rejected_without_changes(five_sources):
    ledger, observer = five_sources
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match="SAME-SESSION"):
        paper_bridge.bootstrap(ledger, observer, SESSION, FIVE, local(18, 15) + timedelta(days=1))
    assert ledger.read_bytes() == before


def _source_queue(names: list[str], started: datetime, generated: datetime) -> dict:
    preview = {
        "source": "YAHOO+NASDAQ_CONSENSUS_SECONDARY_PREVIEW_ONLY",
        "asof": SOURCE,
        "earliest_next_weekday": TARGET,
        "next_weekday_is_not_holiday_certified": True,
        "last_earnings_date": "2026-09-15",
        "next_earnings_date": "2026-12-15",
        "observed_at_utc": (started + timedelta(minutes=1)).isoformat(),
    }
    return {
        "schema": roster.QUEUE_SCHEMA,
        "status": "RESEARCH_QUEUE_ONLY_NO_TRADE_AUTHORITY",
        "session": SOURCE,
        "model_calls": 0,
        "paper_orders": 0,
        "portfolio_reads": 0,
        "portfolio_writes": 0,
        "broker_connected": False,
        "earliest_next_weekday": TARGET,
        "earnings_preview_policy": "TWO_SECONDARY_CONSENSUS_PREVIEW_NOT_NEXT_SESSION_AUTHORITY",
        "generated_utc": generated.isoformat(),
        "scan_started_utc": started.isoformat(),
        "next_session_research_watchlist": names,
        "candidate_count": len(names),
        "candidates": [
            {
                "ticker": name,
                "asof": SOURCE,
                "earnings_verification": "NOT_CAPTURED_FOR_NEXT_SESSION",
                "execution_authority": "NONE_RESEARCH_ONLY",
                "earnings_preview": dict(preview),
            }
            for name in names
        ],
    }


def _prep_roster(tmp_path, names):
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy2(Path(__file__).resolve().parents[1] / roster.SCRIPT, project / roster.SCRIPT)
    root = tmp_path / "swing"
    source = root / "runs" / SOURCE
    source.mkdir(parents=True)
    started = datetime(2026, 10, 30, 22, 40, tzinfo=UTC)
    generated = datetime(2026, 10, 30, 23, 0, tzinfo=UTC)
    (source / "queue.json").write_text(json.dumps(_source_queue(names, started, generated)))
    return project, root, tmp_path / "observations", datetime(2026, 10, 30, 23, 39, tzinfo=UTC)


def test_five_roster_is_accepted_by_installed_real_observer_for_monday(tmp_path):
    project, root, observer, current = _prep_roster(tmp_path, FIVE)
    out = roster.stage(
        project=project,
        root=root,
        observer=observer,
        source_day=SOURCE,
        target_day=TARGET,
        now=current,
    )
    assert out["count"] == 5
    assert out["watchlist"] == FIVE
    assert out["paper_orders"] == 0 and out["broker_connected"] is False
    import importlib.util

    spec = importlib.util.spec_from_file_location("test_roster_sch", project / roster.SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.watchlist_for_session(
        TARGET, rosters=root / "rosters", queues=root / "runs"
    ) == tuple(FIVE)
    assert not observer.exists()


def test_shortlist_of_three_is_rejected_by_stager_without_files(tmp_path):
    project, root, observer, current = _prep_roster(tmp_path, FIVE[:3])
    with pytest.raises(ValueError, match="4-8"):
        roster.stage(
            project=project,
            root=root,
            observer=observer,
            source_day=SOURCE,
            target_day=TARGET,
            now=current,
        )
    assert not (root / "rosters" / (TARGET + ".json")).exists()


def test_stager_rejects_tampered_queue_after_approval(tmp_path):
    project, root, observer, current = _prep_roster(tmp_path, FIVE)
    roster.stage(
        project=project,
        root=root,
        observer=observer,
        source_day=SOURCE,
        target_day=TARGET,
        now=current,
    )
    source = root / "runs" / SOURCE / "queue.json"
    source.write_text(source.read_text() + " ")
    import importlib.util

    spec = importlib.util.spec_from_file_location("test_roster_sch2", project / roster.SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with pytest.raises(ValueError, match="Source scanner queue hash changed"):
        mod.watchlist_for_session(TARGET, rosters=root / "rosters", queues=root / "runs")


def test_roster_staging_is_not_allowed_past_future_preopen(tmp_path):
    project, root, observer, _ = _prep_roster(tmp_path, FIVE)
    future = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="past the next session"):
        roster.stage(
            project=project,
            root=root,
            observer=observer,
            source_day=SOURCE,
            target_day=TARGET,
            now=future,
        )

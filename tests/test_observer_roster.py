"""Focused, no-network observer roster migration tests; zero trading functions imported."""

import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

BASE = Path(__file__).resolve().parent
if not (BASE / "KE_Observer_Scheduler_2026-10-26.py").exists():
    BASE = BASE.parent
spec = importlib.util.spec_from_file_location(
    "ke_scheduler_roster_test", BASE / "KE_Observer_Scheduler_2026-10-26.py"
)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)

NAMES = ["MU", "RNG", "XNCR", "GEO", "TVTX", "BWIN", "EQNR", "RGA"]
DAY = "2026-10-29"
SOURCE = "2026-10-28"
NY = ZoneInfo("America/New_York")


def setup_roster(tmp_path, *, names=None, approved="2026-10-28T23:10:00+00:00"):
    names = names if names is not None else NAMES
    roots = tmp_path / "rosters"
    queues = tmp_path / "runs"
    source = queues / SOURCE / "queue.json"
    source.parent.mkdir(parents=True)
    data = {
        "schema": "koral-swing-research-queue-v1",
        "session": SOURCE,
        "status": "RESEARCH_QUEUE_ONLY_NO_TRADE_AUTHORITY",
        "model_calls": 0,
        "paper_orders": 0,
        "portfolio_reads": 0,
        "portfolio_writes": 0,
        "broker_connected": False,
        "candidate_count": len(names),
        "next_session_research_watchlist": list(names),
        "candidates": [
            {
                "ticker": x,
                "execution_authority": "NONE_RESEARCH_ONLY",
                "earnings_verification": "NOT_CAPTURED_FOR_NEXT_SESSION",
            }
            for x in names
        ],
    }
    source.write_text(json.dumps(data))
    roster = {
        "schema": m.ROSTER_SCHEMA,
        "session": DAY,
        "source_session": SOURCE,
        "queue_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "watchlist": names,
        "mode": "OBSERVE_ONLY",
        "trading_enabled": False,
        "approved_by_operator": True,
        "approved_utc": approved,
    }
    roots.mkdir(parents=True)
    p = roots / (DAY + ".json")
    p.write_text(json.dumps(roster))
    return roots, queues, source, p


def test_only_predeclared_future_session_uses_eight_stock_roster(tmp_path):
    rosters, queues, _, _ = setup_roster(tmp_path)
    assert m.watchlist_for_session(DAY, rosters=rosters, queues=queues) == tuple(NAMES)
    for undeclared in (SOURCE, "2026-10-30"):
        with pytest.raises(ValueError, match="DECLARED_ROSTER_MISSING"):
            m.watchlist_for_session(undeclared, rosters=rosters, queues=queues)


def test_queue_tampering_fails_closed(tmp_path):
    rosters, queues, source, _ = setup_roster(tmp_path)
    raw = json.loads(source.read_text())
    raw["model_calls"] = 1
    source.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="hash changed"):
        m.watchlist_for_session(DAY, rosters=rosters, queues=queues)


def test_operator_roster_cannot_backdate_approval(tmp_path):
    rosters, queues, _, _ = setup_roster(tmp_path, approved="2026-10-29T13:00:00+00:00")
    with pytest.raises(ValueError, match="after the preopen"):
        m.watchlist_for_session(DAY, rosters=rosters, queues=queues)


def test_wrong_tickers_and_trade_authority_are_never_accepted(tmp_path):
    rosters, queues, source, p = setup_roster(tmp_path)
    o = json.loads(p.read_text())
    o["watchlist"] = ["COST", "JPM", "WMT"]
    p.write_text(json.dumps(o))
    with pytest.raises(ValueError, match="does not match"):
        m.watchlist_for_session(DAY, rosters=rosters, queues=queues)
    o = json.loads(p.read_text())
    o["watchlist"] = NAMES
    o["trading_enabled"] = True
    p.write_text(json.dumps(o))
    with pytest.raises(ValueError, match="Unapproved"):
        m.watchlist_for_session(DAY, rosters=rosters, queues=queues)


def test_corrupt_or_broken_symlink_roster_not_treated_as_no_roster(tmp_path):
    roots = tmp_path / "rosters"
    roots.mkdir()
    plan = roots / (DAY + ".json")
    try:
        plan.symlink_to(roots / "nonexistent.json")
    except OSError:
        pytest.skip("Symlink privilege unavailable")
    with pytest.raises(ValueError, match="Unsafe"):
        m.watchlist_for_session(DAY, rosters=roots, queues=tmp_path / "runs")


def test_observer_run_tick_uses_eight_names_and_verifies_archive_sha(tmp_path, monkeypatch):
    rosters, queues, source, p = setup_roster(tmp_path)
    monkeypatch.setattr(m, "ROSTERS", rosters)
    monkeypatch.setattr(m, "QUEUES", queues)
    root = tmp_path / "observations"
    project = tmp_path / "project"
    project.mkdir()
    seen = []

    def fake_run(command, **kwargs):
        assert command[-len(NAMES) :] == NAMES
        seen.append(command)
        dst = root / "market" / f"{DAY}.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "session": DAY,
            "mode": "OBSERVE_ONLY",
            "orders": [],
            "ohlc_usd": {
                x: {"open": 100, "close": 100, "high": 101, "low": 99} for x in NAMES + ["SPY"]
            },
        }
        dst.write_text(json.dumps(data))
        output = {
            "mode": "OBSERVE_ONLY",
            "status": "CAPTURED",
            "phase": "close",
            "session": DAY,
            "orders": 0,
            "symbols": NAMES + ["SPY"],
            "sha256": hashlib.sha256(dst.read_bytes()).hexdigest(),
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(output), stderr="")

    t = datetime(2026, 10, 29, 18, 0, tzinfo=NY)
    result = m.run_tick(now=t, project=project, home=root, runner=fake_run, require_preflight=False)
    assert result["status"] == "CAPTURED" and result["watchlist"] == NAMES
    assert result["trading_enabled"] is False and result["account_writes"] == 0
    assert len(seen) == 1
    assert (root / "scheduler_runs" / f"{DAY}.jsonl").is_file()


def test_wrong_archive_universe_fails_even_with_matching_hash_receipt(tmp_path, monkeypatch):
    rosters, queues, _, _ = setup_roster(tmp_path)
    monkeypatch.setattr(m, "ROSTERS", rosters)
    monkeypatch.setattr(m, "QUEUES", queues)
    root = tmp_path / "observations"
    project = tmp_path / "project"
    project.mkdir()

    def broken(command, **kwargs):
        dst = root / "market" / f"{DAY}.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "session": DAY,
            "mode": "OBSERVE_ONLY",
            "orders": [],
            "ohlc_usd": {"COST": {"open": 100}},
        }
        dst.write_text(json.dumps(data))
        return SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps(
                {
                    "mode": "OBSERVE_ONLY",
                    "status": "ALREADY_CAPTURED",
                    "phase": "close",
                    "session": DAY,
                    "orders": 0,
                    "sha256": hashlib.sha256(dst.read_bytes()).hexdigest(),
                }
            ),
        )

    result = m.run_tick(
        now=datetime(2026, 10, 29, 18, tzinfo=NY),
        project=project,
        home=root,
        runner=broken,
        require_preflight=False,
    )
    assert result["status"] == "FAILED_CLOSED"
    assert "declared research universe" in result["failure"]


def test_heartbeat_health_checks_eight_archive_names(tmp_path, monkeypatch):
    rosters, queues, _, _ = setup_roster(tmp_path)
    monkeypatch.setattr(m, "ROSTERS", rosters)
    monkeypatch.setattr(m, "QUEUES", queues)
    home = tmp_path / "observe"
    home.mkdir()
    now = datetime(2026, 10, 29, 18, 30, tzinfo=NY)
    (home / "scheduler_heartbeat.json").write_text(
        json.dumps(
            {"utc": now.astimezone(UTC).isoformat(), "watchlist": NAMES, "status": "CAPTURED"}
        )
    )
    result = m.check_health(now=now, home=home)
    assert result["tickers"] == NAMES
    assert "WATCHLIST_DRIFT" not in result["issues"]
    assert "MISSING_PRECLOSE_SNAPSHOT" in result["issues"]


def test_no_roster_refuses_existing_pilot_watchlist(tmp_path):
    with pytest.raises(ValueError, match="DECLARED_ROSTER_MISSING"):
        m.watchlist_for_session(DAY, rosters=tmp_path / "missing", queues=tmp_path / "other")

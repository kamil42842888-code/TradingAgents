"""Focused integration with REAL V3.1 classes, never user ledger/network/LLM.

Executable on user's existing .venv. Isolated tmp_path source/portfolio inputs.
This is the NEW controller's end-to-end release gate, not a rerun of old suites.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tradingagents.paper.autopilot import paper_bridge, paper_forward
from tradingagents.paper.autopilot.state import atomic_write, create_new, read
from tradingagents.paper.conditional import TradeIntent, evidence_hash

NY = ZoneInfo("America/New_York")
BASE = "2026-10-30"
MON = "2026-11-02"
TUE = "2026-11-03"
WED = "2026-11-04"
THU = "2026-11-05"
NAMES = ["TWST", "CRWD", "MRVL", "AVAH", "ITEK"]


def ny(session, h, m=10):
    return datetime.fromisoformat(f"{session}T{h:02d}:{m:02d}:00").replace(tzinfo=NY)


def utc(value):
    return value.astimezone(UTC).isoformat()


def put_sources(observer: Path, session: str, *, missing=None, unverified=()):
    missing = set(missing or [])
    for phase in ("preopen", "preclose"):
        if phase in missing:
            continue
        at = ny(session, 9, 10) if phase == "preopen" else ny(session, 17, 10)
        payload = {
            "session": session,
            "phase": phase,
            "observed_utc": utc(at),
            "source_policy": "TWO_SECONDARY_FEEDS_MUST_AGREE_OR_BLOCK",
            "earnings": {
                symbol: {
                    "asof_date": session,
                    "next_earnings_date": "2026-12-15",
                    "last_earnings_date": "2026-09-15",
                    "source": "YAHOO+NASDAQ_CONSENSUS_SECONDARY"
                    if symbol not in unverified
                    else "UNVERIFIED_EARNINGS_SOURCE",
                    "verified": symbol not in unverified,
                    "observed_at_utc": utc(at),
                }
                for symbol in NAMES
            },
        }
        path = observer / "calendar" / session / (phase + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    if "close" not in missing:
        ohlc = {t: {"open": 101.0, "high": 103.0, "low": 99.0, "close": 102.0} for t in NAMES}
        ohlc["SPY"] = {"open": 600.0, "high": 605.0, "low": 598.0, "close": 602.0}
        payload = {
            "schema": "koral-observation-v1",
            "mode": "OBSERVE_ONLY",
            "session": session,
            "phase": "close",
            "capture_started_utc": utc(ny(session, 17, 45)),
            "capture_completed_utc": utc(ny(session, 17, 48)),
            "source": "YAHOO_SECONDARY_NOT_EXCHANGE_CERTIFIED",
            "ohlc_usd": ohlc,
            "fx_gbp_per_usd_open": 0.75,
            "fx_gbp_per_usd_close": 0.75,
            "corporate_actions": "QUERIED_NO_UNMODELLED_EVENTS_REPORTED",
            "orders": [],
        }
        path = observer / "market" / (session + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


@pytest.fixture
def baselined(tmp_path):
    ledger = tmp_path / "paper" / "v3_autopilot" / "portfolio.json"
    create_new(ledger, "2026-10-22", ["AAPL", "MSFT", "NVDA"])
    observer = tmp_path / "observations" / "koral_v3"
    put_sources(observer, BASE)
    result = paper_bridge.bootstrap(ledger, observer, BASE, NAMES, ny(BASE, 18, 15))
    assert result["status"] == "FORWARD_PAPER_BASELINE_BOUND"
    assert read(ledger).auto_paper_enabled is False
    return ledger, observer


def test_new_observer_session_marks_existing_ledger_without_trades(baselined):
    ledger, observer = baselined
    original = read(ledger)
    original_audit = original.audit[0]
    put_sources(observer, MON)
    preview = paper_forward.inspect(ledger, observer, MON, NAMES, ny(MON, 18, 15))
    assert preview["new_session_to_mark"] is True
    result = paper_forward.run_once(ledger, observer, MON, NAMES, ny(MON, 18, 15))
    state = read(ledger)
    assert result["mark"] == "FORWARD_PAPER_SESSION_MARKED"
    assert result["model_attempts"] == 0
    assert state.created_utc == original.created_utc
    assert state.audit[0] == original_audit
    assert state.portfolio.book.started == "2026-10-22"
    assert state.portfolio.book.last_session == MON
    assert state.portfolio.book.equity() == 10000.0
    assert state.auto_paper_enabled is False
    assert state.portfolio.intents == {} and state.portfolio.book.orders == []
    assert state.portfolio.book.positions == {}
    assert (ledger.parent / "observer_sources" / MON / "market.json").exists()
    before = ledger.read_bytes()
    again = paper_forward.run_once(ledger, observer, MON, NAMES, ny(MON, 18, 20))
    assert again["mark"] == "ALREADY_MARKED"
    assert ledger.read_bytes() == before


def test_missing_preopen_or_changed_source_cannot_mutate_book(baselined):
    ledger, observer = baselined
    put_sources(observer, MON, missing={"preopen"})
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match="preopen"):
        paper_forward.run_once(ledger, observer, MON, NAMES, ny(MON, 18, 15))
    assert ledger.read_bytes() == before
    put_sources(observer, MON)
    market = observer / "market" / (MON + ".json")
    raw = json.loads(market.read_text())
    raw["ohlc_usd"]["TWST"]["high"] = 90  # impossible OHLC
    market.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="Invalid completed OHLC"):
        paper_forward.run_once(ledger, observer, MON, NAMES, ny(MON, 18, 15))
    assert ledger.read_bytes() == before


def test_missing_weekday_on_flat_account_skips_without_backfill(baselined):
    ledger, observer = baselined
    put_sources(observer, TUE)
    paper_forward.run_once(ledger, observer, TUE, NAMES, ny(TUE, 18, 15))
    state = read(ledger)
    assert not state.portfolio.book.positions and not state.portfolio.book.orders
    assert any(e["kind"] == "UNOBSERVED_FLAT_GAP_SKIPPED" for e in state.audit)
    assert not (ledger.parent / "market_evidence" / (MON + ".json")).exists()


def test_disabled_account_rejects_model_even_when_budget_requested(baselined):
    ledger, observer = baselined
    put_sources(observer, MON)

    def forbidden(*args, **kwargs):
        raise AssertionError("No AI permitted while disabled")

    result = paper_forward.run_once(
        ledger, observer, MON, NAMES, ny(MON, 18, 15), ai_budget=1, graph_factory=forbidden
    )
    assert result["research"] == "PAPER_DISABLED_OR_RISK_HALTED"
    assert result["model_attempts"] == 0
    assert read(ledger).ai_receipts == {}


def real_conditional_report(root, ticker, session):
    report = '{"source":"mock-committee","result":"CONDITIONAL_LONG"}\n'
    path = root / "reports" / session / ticker / "source.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(report.encode())
    digest = evidence_hash(report)
    plan = TradeIntent(
        intent_id="KEI-V3_FORWARD_TEST",
        ticker=ticker,
        decision_date=session,
        expiry_date="2026-11-20",
        rating="Overweight",
        side="ENTER_LONG",
        entry_rule="CLOSES_ABOVE",
        trigger_usd=100.0,
        required_closes=2,
        stop_loss_usd=85.0,
        stop_is_executable=True,
        initial_allocation_fraction=0.08,
        max_holding_sessions=20,
        max_entry_gap_fraction=0.025,
        sector="Healthcare",
        report_sha256=digest,
    )
    return (
        {
            "decision": "CONDITIONAL_LONG",
            "source_file": str(path.relative_to(root)),
            "source_sha256": digest,
        },
        plan,
        report,
    )


def test_valid_model_plan_watches_then_only_future_closes_queue_and_fill(baselined):
    ledger, observer = baselined
    state = read(ledger)
    state.set_running(True)  # ONLY A tmp_path TEST ACCOUNT, never user ledger
    atomic_write(ledger, state)
    put_sources(observer, MON)

    def fake_research(graph, *, ticker, session, bar, root, state):
        assert ticker == NAMES[0] and session == MON
        return real_conditional_report(root, ticker, session)

    result = paper_forward.run_once(
        ledger,
        observer,
        MON,
        NAMES,
        ny(MON, 18, 15),
        ai_budget=1,
        graph_factory=lambda root: object(),
        research_fn=fake_research,
    )
    state = read(ledger)
    assert result["model_attempts"] == 1
    assert len(state.portfolio.intents) == 1
    assert not state.portfolio.book.orders
    assert not state.portfolio.book.positions
    # Repeating the same command never purchases another model analysis.
    another = paper_forward.run_once(
        ledger,
        observer,
        MON,
        NAMES,
        ny(MON, 18, 16),
        ai_budget=1,
        graph_factory=lambda root: (_ for _ in ()).throw(AssertionError("REPEAT")),
    )
    assert another["research"] == "DAILY_AI_BUDGET_ALREADY_USED"
    assert another["model_attempts"] == 0
    # Real conditional book model: two FUTURE completed closes, THEN next open.
    for day in (TUE, WED, THU):
        put_sources(observer, day)
        paper_forward.run_once(ledger, observer, day, NAMES, ny(day, 18, 15))
    state = read(ledger)
    assert state.portfolio.book.last_session == THU
    assert state.portfolio.book.positions.get("TWST") is not None
    assert state.portfolio.intents["KEI-V3_FORWARD_TEST"]["status"] == "FILLED"
    assert state.portfolio.book.cash_gbp < 10000
    assert any(e["type"] == "BUY_FILLED" for e in state.portfolio.book.events)
    assert not any(e.get("kind") == "BACKFILL_MODELED_STANDING_RISK" for e in state.audit)


def test_tampering_accepted_report_refuses_further_processing(baselined):
    ledger, observer = baselined
    state = read(ledger)
    state.set_running(True)
    atomic_write(ledger, state)
    put_sources(observer, MON)

    def research(graph, *, ticker, session, bar, root, state):
        return real_conditional_report(root, ticker, session)

    paper_forward.run_once(
        ledger,
        observer,
        MON,
        NAMES,
        ny(MON, 18, 15),
        ai_budget=1,
        graph_factory=lambda root: object(),
        research_fn=research,
    )
    file = ledger.parent / "reports" / MON / "TWST" / "source.json"
    file.write_bytes(file.read_bytes() + b"TAMPER")
    put_sources(observer, TUE)
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match="Previously accepted AI report missing or tampered"):
        paper_forward.run_once(ledger, observer, TUE, NAMES, ny(TUE, 18, 15))
    assert ledger.read_bytes() == before

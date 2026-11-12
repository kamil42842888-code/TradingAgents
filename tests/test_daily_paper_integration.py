"""New integration only: real installed V3.1 classes, synthetic temp evidence.

No user portfolio, network or paid model is used. Models are injected fakes;
the actual ConditionalBook, source-binding and atomic writes remain real.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from tradingagents.paper.autopilot import (
    daily_roster,
    daily_support as support,
    paper_bridge,
    paper_forward,
)
from tradingagents.paper.autopilot.state import atomic_write, create_new, read
from tradingagents.paper.conditional import TradeIntent, evidence_hash

NAMES = ["TWST", "CRWD", "MRVL", "AVAH", "TTEK"]
BASE = "2026-11-02"
NEXT = "2026-11-03"


def at(day, h=18, m=15):
    return datetime.fromisoformat(f"{day}T{h:02d}:{m:02d}:00").replace(tzinfo=support.NY)


def stamp(value):
    return value.astimezone(UTC).isoformat()


def sources(home, day, names=NAMES):
    for phase, h in (("preopen", 9), ("preclose", 17)):
        ts = stamp(at(day, h, 10))
        obj = {
            "session": day,
            "phase": phase,
            "observed_utc": ts,
            "source_policy": "TWO_SECONDARY_FEEDS_MUST_AGREE_OR_BLOCK",
            "earnings": {
                t: {
                    "asof_date": day,
                    "next_earnings_date": "2026-12-15",
                    "last_earnings_date": "2026-09-15",
                    "source": "YAHOO+NASDAQ_CONSENSUS_SECONDARY",
                    "verified": True,
                    "observed_at_utc": ts,
                }
                for t in names
            },
        }
        p = home / "calendar" / day / (phase + ".json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(support.canonical(obj))
    bars = {t: {"open": 101.0, "high": 103.0, "low": 99.0, "close": 102.0} for t in [*names, "SPY"]}
    obj = {
        "schema": "koral-observation-v1",
        "mode": "OBSERVE_ONLY",
        "orders": [],
        "session": day,
        "phase": "close",
        "capture_started_utc": stamp(at(day, 17, 45)),
        "capture_completed_utc": stamp(at(day, 17, 48)),
        "source": "YAHOO_SECONDARY_NOT_EXCHANGE_CERTIFIED",
        "corporate_actions": "QUERIED_NO_UNMODELLED_EVENTS_REPORTED",
        "ohlc_usd": bars,
        "fx_gbp_per_usd_open": 0.75,
        "fx_gbp_per_usd_close": 0.75,
    }
    p = home / "market" / (day + ".json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(support.canonical(obj))


@pytest.fixture
def account(tmp_path):
    ledger = tmp_path / "paper" / "portfolio.json"
    observer = tmp_path / "observations"
    create_new(ledger, "2026-10-22", ["AAPL", "MSFT", "NVDA"])
    sources(observer, BASE)
    paper_bridge.bootstrap(ledger, observer, BASE, NAMES, at(BASE))
    state = read(ledger)
    state.set_running(True)
    atomic_write(ledger, state)
    return ledger, observer


def fake_research(decisions, calls, *, positive=False):
    def research(graph, *, ticker, session, bar, root, state):
        calls.append(ticker)
        rating = decisions.get(ticker, "Sell")
        text = json.dumps({"ticker": ticker, "session": session, "rating": rating}) + "\n"
        file = root / "reports" / session / ticker / "source.json"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(text.encode())
        receipt = {
            "rating": rating,
            "decision": "NO_TRADE",
            "session": session,
            "reason": "No positive allocation" if rating == "Sell" else "Mock research",
            "source_file": str(file.relative_to(root)),
            "source_sha256": evidence_hash(text),
        }
        plan = None
        if positive and rating == "Buy":
            plan = TradeIntent(
                intent_id="KEI-DAILY_" + ticker,
                ticker=ticker,
                decision_date=session,
                expiry_date="2026-11-20",
                rating="Buy",
                side="ENTER_LONG",
                entry_rule="CLOSES_ABOVE",
                trigger_usd=100.0,
                required_closes=2,
                stop_loss_usd=85.0,
                stop_is_executable=True,
                initial_allocation_fraction=0.08,
                max_holding_sessions=20,
                max_entry_gap_fraction=0.025,
                sector="Technology",
                report_sha256=evidence_hash(text),
            )
            receipt["decision"] = "CONDITIONAL_LONG"
        return receipt, plan, text

    return research


def run(ledger, observer, day, budget=3, **kw):
    return paper_forward.run_once(
        ledger,
        observer,
        day,
        NAMES,
        at(day),
        ai_budget=budget,
        graph_factory=lambda root: object(),
        completion_clock=lambda: at(day),
        config=dict(support.DEFAULTS),
        **kw,
    )


def test_three_companies_continue_after_sell_without_a_fourth_or_repeat(account):
    ledger, observer = account
    sources(observer, NEXT)
    calls = []
    result = run(ledger, observer, NEXT, research_fn=fake_research({"CRWD": "Hold"}, calls))
    assert calls == NAMES[:3] and result["model_attempts"] == 3
    assert len(read(ledger).ai_receipts) == 3
    before = ledger.read_bytes()
    again = run(ledger, observer, NEXT, research_fn=lambda *a, **k: pytest.fail("repeat model"))
    assert again["model_attempts"] == 0 and ledger.read_bytes() == before
    assert not read(ledger).portfolio.book.positions


def test_multiple_positive_reports_still_allow_only_one_new_intent(account):
    ledger, observer = account
    sources(observer, NEXT)
    calls = []
    run(
        ledger,
        observer,
        NEXT,
        research_fn=fake_research(dict.fromkeys(NAMES, "Buy"), calls, positive=True),
    )
    st = read(ledger)
    assert len(calls) == 3 and len(st.portfolio.intents) == 1
    assert list(st.portfolio.intents.values())[0]["status"] == "WATCHING"
    assert st.portfolio.book.orders == [] and st.portfolio.book.positions == {}
    assert sum(e["kind"] == "V3_POLICY_ACCEPTED" for e in st.audit) == 1


def test_failed_attempt_is_not_retried_after_restart(account):
    ledger, observer = account
    sources(observer, NEXT)
    calls = []

    def crash(*args, **kw):
        calls.append(kw["ticker"])
        raise KeyboardInterrupt("simulated unclean analysis")

    with pytest.raises(KeyboardInterrupt):
        run(ledger, observer, NEXT, research_fn=crash)
    assert read(ledger).ai_receipts[NEXT + ":TWST"]["status"] == "IN_PROGRESS"
    run(ledger, observer, NEXT, research_fn=fake_research({}, calls))
    assert calls == NAMES[:3]
    assert read(ledger).ai_receipts[NEXT + ":TWST"]["status"] == "IN_PROGRESS"


def test_disabled_account_marks_but_does_not_call_models(account):
    ledger, observer = account
    st = read(ledger)
    st.set_running(False)
    atomic_write(ledger, st)
    sources(observer, NEXT)
    result = run(
        ledger, observer, NEXT, research_fn=lambda *a, **k: pytest.fail("model while disabled")
    )
    assert result["model_attempts"] == 0
    assert read(ledger).portfolio.book.last_session == NEXT


def test_missing_sources_do_not_mutate_account(account):
    ledger, observer = account
    sources(observer, NEXT)
    (observer / "calendar" / NEXT / "preclose.json").unlink()
    old = ledger.read_bytes()
    with pytest.raises(ValueError):
        run(ledger, observer, NEXT, research_fn=fake_research({}, []))
    assert ledger.read_bytes() == old


def test_flat_gap_preserves_account_history_without_invented_marks(account):
    ledger, observer = account
    old = read(ledger)
    sources(observer, "2026-11-05")
    run(ledger, observer, "2026-11-05", budget=0)
    st = read(ledger)
    assert st.created_utc == old.created_utc
    assert st.audit[0] == old.audit[0]
    assert st.portfolio.book.equity() == 10000
    assert len(st.portfolio.book.equity_history) == 2
    assert any(e["kind"] == "UNOBSERVED_FLAT_GAP_SKIPPED" for e in st.audit)
    assert not (ledger.parent / "market_evidence" / "2026-11-03.json").exists()


def test_gap_with_active_intent_refuses_catchup_purchase(account):
    ledger, observer = account
    sources(observer, NEXT)
    run(
        ledger,
        observer,
        NEXT,
        budget=1,
        research_fn=fake_research({"TWST": "Buy"}, [], positive=True),
    )
    sources(observer, "2026-11-05")
    old = ledger.read_bytes()
    with pytest.raises(ValueError, match="Unprocessed sessions"):
        run(ledger, observer, "2026-11-05", budget=0)
    assert ledger.read_bytes() == old


def test_source_tamper_is_rejected_without_new_requests(account):
    ledger, observer = account
    sources(observer, NEXT)
    run(ledger, observer, NEXT, research_fn=fake_research({}, []))
    file = ledger.parent / "observer_sources" / NEXT / "market.json"
    file.write_bytes(file.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Bound observer"):
        run(ledger, observer, NEXT, research_fn=lambda *a, **k: pytest.fail("model after tamper"))


def test_pending_symbol_survives_source_based_roster_rotation(account, tmp_path):
    ledger, observer = account
    sources(observer, NEXT)
    run(
        ledger,
        observer,
        NEXT,
        budget=1,
        research_fn=fake_research({"TWST": "Buy"}, [], positive=True),
    )
    state = read(ledger)
    selected = daily_roster.choose_names(
        state.watchlist, support.active_symbols(state), ["AAPL", "MSFT", "GOOGL", "AMZN"]
    )
    assert selected[0] == "TWST"
    assert set(selected) == {"TWST", "AAPL", "MSFT", "GOOGL", "AMZN"}


def test_new_long_plan_still_needs_future_confirmations_and_fill(account):
    ledger, observer = account
    sources(observer, NEXT)
    run(
        ledger,
        observer,
        NEXT,
        budget=1,
        research_fn=fake_research({"TWST": "Buy"}, [], positive=True),
    )
    for day in ("2026-11-04", "2026-11-05"):
        sources(observer, day)
        run(ledger, observer, day, budget=0)
    st = read(ledger)
    assert len(st.portfolio.book.orders) == 1 and not st.portfolio.book.positions
    sources(observer, "2026-11-06")
    run(ledger, observer, "2026-11-06", budget=0)
    st = read(ledger)
    assert "TWST" in st.portfolio.book.positions
    assert all(p["shares"] > 0 for p in st.portfolio.book.positions.values())

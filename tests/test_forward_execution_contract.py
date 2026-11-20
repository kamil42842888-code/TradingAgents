"""Offline joined execution contract: source reports to managed paper exits.

Only the committee/model responses and market observations are synthetic. The
actual source archiver, structured policy, managed forward controller, ledger,
conditional orders and fill/exit accounting run against pytest temporary paths.
The suite's autouse fixture blocks both socket and curl network access.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from tests.test_daily_paper_integration import (
    NAMES,
    NEXT,
    account as account,
    at,
    sources,
)
from tradingagents.paper.autopilot import daily_support, paper_forward, runner
from tradingagents.paper.autopilot.decision import AIExecutionProposal
from tradingagents.paper.autopilot.state import read

TICKER = NAMES[0]
ENTRY = "2 consecutive closes above $100.00"
TRADER = "Use an executable stop-loss at $85.00 for this conditional paper entry."
MANDATE = """KORAL_V3_EXECUTION_V1
action=CONDITIONAL_LONG
trigger_usd=100.00
stop_usd=85.00
required_closes=2
allocation_fraction=0.08
expiry_days=14
holding_sessions=2
END_KORAL_V3_EXECUTION"""
PM = (
    "Rating: Overweight.\n"
    f"Enter only after {ENTRY}. Use a firm executable stop at $85.00. "
    "Approve an initial allocation of 8%, an entry expiry of 14 calendar days, "
    "and a maximum holding period of 2 sessions.\n" + MANDATE
)


class SyntheticCommittee:
    """External model stand-in; deliberately does not construct TradeIntent."""

    def __init__(self, *, pm=PM, holding_sessions=2, entry_quote=ENTRY, trader=TRADER):
        self.pm = pm
        self.holding_sessions = holding_sessions
        self.entry_quote = entry_quote
        self.trader = trader
        self.deep_thinking_llm = self
        self.committee_calls = 0
        self.extraction_calls = 0

    def propagate(self, ticker, session, portfolio):
        assert (ticker, session) == (TICKER, NEXT)
        assert portfolio.positions == [] and portfolio.cash is None
        self.committee_calls += 1
        return {
            "final_trade_decision": self.pm,
            "trader_investment_plan": self.trader,
        }, "Overweight"

    def save_reports(self, final_state, ticker, save_path):
        assert ticker == TICKER
        (save_path / "complete_report.md").write_text(
            final_state["final_trade_decision"] + "\n\n" + self.trader,
            encoding="utf-8",
        )

    def with_structured_output(self, schema):
        assert schema is AIExecutionProposal
        return self

    def invoke(self, prompt):
        assert self.pm in prompt and self.trader in prompt
        self.extraction_calls += 1
        return AIExecutionProposal(
            action="CONDITIONAL_LONG",
            rating="Overweight",
            entry_quote=self.entry_quote,
            stop_quote=self.trader,
            trigger_usd=100.0,
            stop_usd=85.0,
            required_closes=2,
            expiry_days=14,
            holding_sessions=self.holding_sessions,
            allocation_fraction=0.08,
            rationale="Synthetic extraction of the original conditional mandate.",
        )


def process(ledger, observer, day, *, committee=None):
    def research(graph, **kwargs):
        return runner._research_one(
            graph,
            **kwargs,
            sector_info=lambda ticker: (
                "Technology",
                {"currency": "USD", "averageVolume": 1_000_000},
            ),
        )

    def graph_factory(root):
        assert committee is not None, "No further model calls are allowed"
        return committee

    return paper_forward.run_once(
        ledger,
        observer,
        day,
        NAMES,
        at(day),
        ai_budget=1 if committee is not None else 0,
        graph_factory=graph_factory,
        research_fn=research,
        completion_clock=lambda: at(day),
        config=dict(daily_support.DEFAULTS),
    )


@pytest.mark.parametrize("exit_kind", ["adverse_gap_stop", "holding_period"])
def test_report_contract_reaches_prospective_paper_entry_and_exit(account, exit_kind):
    ledger, observer = account
    committee = SyntheticCommittee()
    sources(observer, NEXT)
    result = process(ledger, observer, NEXT, committee=committee)
    state = read(ledger)
    assert result["model_attempts"] == 1
    assert committee.committee_calls == committee.extraction_calls == 1
    assert len(state.portfolio.intents) == 1
    intent_id, intent = next(iter(state.portfolio.intents.items()))
    assert intent["status"] == "WATCHING" and intent["consecutive"] == 0
    assert not state.portfolio.book.orders and not state.portfolio.book.positions

    receipt = state.ai_receipts[f"{NEXT}:{TICKER}"]
    source_file = ledger.parent / receipt["source_file"]
    source_bytes = source_file.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    assert source_hash == receipt["source_sha256"] == intent["plan"]["report_sha256"]
    original_report = json.loads(source_bytes)
    assert original_report["portfolio_manager"] == PM
    assert original_report["trader"] == TRADER
    assert (original_report["ticker"], original_report["session"]) == (TICKER, NEXT)

    before = ledger.read_bytes()
    repeated = process(ledger, observer, NEXT, committee=committee)
    assert repeated["model_attempts"] == 0 and ledger.read_bytes() == before
    assert committee.committee_calls == committee.extraction_calls == 1

    for count, day in enumerate(("2026-11-04", "2026-11-05"), start=1):
        sources(observer, day)
        process(ledger, observer, day)
        state = read(ledger)
        intent = state.portfolio.intents[intent_id]
        assert intent["consecutive"] == count
        assert intent["status"] == ("WATCHING" if count == 1 else "QUEUED")
        assert not state.portfolio.book.positions
        assert len(state.portfolio.book.orders) == (0 if count == 1 else 1)
    queued = state.portfolio.book.orders[0]
    assert queued["created"] == "2026-11-05" and queued["source_intent_id"] == intent_id

    sources(observer, "2026-11-06")
    process(ledger, observer, "2026-11-06")
    state = read(ledger)
    buys = [event for event in state.portfolio.book.events if event["type"] == "BUY_FILLED"]
    assert len(buys) == 1 and buys[0]["date"] == "2026-11-06"
    position = state.portfolio.book.positions[TICKER]
    assert position["shares"] > 0 and position["entry_usd"] == pytest.approx(101.0 * 1.001)
    assert position["cost_gbp"] <= 800.0 and position["planned_risk_gbp"] <= 200.0
    assert state.portfolio.intents[intent_id]["status"] == "FILLED"
    assert not state.portfolio.book.orders

    sources(observer, "2026-11-09")
    if exit_kind == "adverse_gap_stop":
        # Set the new, still unconsumed synthetic observation before publishing
        # it through the real controller. Never alter previously bound evidence.
        market_file = observer / "market" / "2026-11-09.json"
        market = json.loads(market_file.read_bytes())
        market["ohlc_usd"][TICKER] = {"open": 80.0, "high": 84.0, "low": 79.0, "close": 82.0}
        market_file.write_bytes(daily_support.canonical(market))
    process(ledger, observer, "2026-11-09")
    final_day = "2026-11-09"
    if exit_kind == "holding_period":
        state = read(ledger)
        assert TICKER in state.portfolio.book.positions
        assert len(state.portfolio.book.orders) == 1
        sell_order = state.portfolio.book.orders[0]
        assert sell_order["side"] == "SELL" and sell_order["rating"] == "TIME_EXIT"
        assert sell_order["created"] == "2026-11-09"
        final_day = "2026-11-10"
        sources(observer, final_day)
        process(ledger, observer, final_day)

    state = read(ledger)
    book = state.portfolio.book
    assert not book.positions and not book.orders and len(book.completed) == 1
    trade = book.completed[0]
    assert (trade["entry_date"], trade["exit_date"]) == ("2026-11-06", final_day)
    assert trade["reason"] == ("STOP" if exit_kind == "adverse_gap_stop" else "SIGNAL")
    if exit_kind == "adverse_gap_stop":
        assert trade["exit_usd"] == pytest.approx(80.0 * 0.999)
        assert trade["exit_usd"] < intent["plan"]["stop_loss_usd"]
        assert trade["pnl_gbp"] < 0
    assert book.cash_gbp == pytest.approx(10_000 + trade["pnl_gbp"], abs=0.005)
    assert len([event for event in book.events if event["type"] == "SELL_FILLED"]) == 1
    assert len([event for event in book.events if event["type"] == "BUY_FILLED"]) == 1
    assert source_file.read_bytes() == source_bytes
    assert not any(event["kind"] == "BACKFILL_MODELED_STANDING_RISK" for event in state.audit)
    for binding in (
        event["details"] for event in state.audit if event["kind"] == "OBSERVER_SOURCE_BOUND"
    ):
        day = binding["date"]
        raw_market = (observer / "market" / f"{day}.json").read_bytes()
        assert hashlib.sha256(raw_market).hexdigest() == binding["market_sha256"]

    before = ledger.read_bytes()
    process(ledger, observer, final_day)
    assert ledger.read_bytes() == before
    assert committee.committee_calls == committee.extraction_calls == 1


@pytest.mark.parametrize(
    "invalid_mandate",
    [
        "missing",
        "invented_holding_period",
        "contradictory_stop",
        "unrelated_entry_price",
        "unrelated_trader_stop_price",
    ],
)
def test_unapproved_model_terms_cannot_create_forward_order(account, invalid_mandate):
    ledger, observer = account
    if invalid_mandate == "missing":
        committee = SyntheticCommittee(pm=PM.replace(MANDATE, ""))
    elif invalid_mandate == "invented_holding_period":
        committee = SyntheticCommittee(holding_sessions=3)
    elif invalid_mandate == "contradictory_stop":
        committee = SyntheticCommittee(
            pm=PM.replace(
                "Use a firm executable stop at $85.00", "Use a firm executable stop at $90.00"
            )
        )
    elif invalid_mandate == "unrelated_entry_price":
        unrelated_quote = "2 consecutive closes above $105.00; current price $100.00"
        committee = SyntheticCommittee(
            pm=PM.replace(ENTRY, unrelated_quote), entry_quote=unrelated_quote
        )
    else:
        committee = SyntheticCommittee(
            trader=(
                "Use an executable stop-loss at $90.00; current price $85.00 "
                "for this conditional paper entry."
            )
        )
    sources(observer, NEXT)
    process(ledger, observer, NEXT, committee=committee)
    state = read(ledger)
    receipt = state.ai_receipts[f"{NEXT}:{TICKER}"]
    assert receipt["decision"] == "NO_TRADE"
    assert receipt["reason"].startswith("policy_rejected_")
    assert not state.portfolio.intents and not state.portfolio.book.orders
    assert not state.portfolio.book.positions
    assert state.portfolio.book.last_session == NEXT
    assert (ledger.parent / receipt["source_file"]).is_file()
    sources(observer, "2026-11-04")
    process(ledger, observer, "2026-11-04")
    state = read(ledger)
    assert not state.portfolio.intents and not state.portfolio.book.orders
    assert not any(event["type"] == "BUY_FILLED" for event in state.portfolio.book.events)

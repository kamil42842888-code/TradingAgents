"""Research restarts must use exactly the evidence already bound by the mark."""

import json

import pytest

from tests.test_daily_paper_integration import (
    NAMES,
    NEXT,
    account as account,
    at,
    fake_research,
    run,
    sources,
)
from tradingagents.paper.autopilot import daily_support as s, paper_forward
from tradingagents.paper.autopilot.state import read


def change_observer_source(observer, phase):
    path = (
        observer / "market" / (NEXT + ".json")
        if phase == "market"
        else observer / "calendar" / NEXT / (phase + ".json")
    )
    payload = json.loads(path.read_bytes())
    if phase == "market":
        payload["ohlc_usd"][NAMES[0]]["close"] = 101.5
    else:
        payload["earnings"][NAMES[0]]["next_earnings_date"] = "2026-12-16"
    path.write_bytes(s.canonical(payload))


@pytest.mark.parametrize("phase", ["market", "preopen", "preclose"])
def test_marked_session_rejects_changed_original_observer_evidence(account, phase):
    ledger, observer = account
    sources(observer, NEXT)
    run(ledger, observer, NEXT, budget=0)
    before = ledger.read_bytes()
    change_observer_source(observer, phase)

    with pytest.raises(ValueError, match="marked session evidence"):
        paper_forward.inspect(ledger, observer, NEXT, NAMES, at(NEXT))
    with pytest.raises(ValueError, match="marked session evidence"):
        run(
            ledger,
            observer,
            NEXT,
            research_fn=lambda *a, **k: pytest.fail("model after source change"),
        )

    assert ledger.read_bytes() == before
    assert read(ledger).ai_receipts == {}


def test_evidence_change_between_preflight_and_processing_is_rejected(account, monkeypatch):
    ledger, observer = account
    sources(observer, NEXT)
    run(ledger, observer, NEXT, budget=0)
    before = ledger.read_bytes()
    original_inspect = paper_forward.inspect

    def inspect_then_change(*args, **kwargs):
        result = original_inspect(*args, **kwargs)
        change_observer_source(observer, "market")
        return result

    monkeypatch.setattr(paper_forward, "inspect", inspect_then_change)
    with pytest.raises(ValueError, match="between preflight and processing"):
        run(
            ledger,
            observer,
            NEXT,
            research_fn=lambda *a, **k: pytest.fail("model after source change"),
        )
    assert ledger.read_bytes() == before


def test_unchanged_mark_can_complete_three_attempts_and_repeat_idempotently(account):
    ledger, observer = account
    sources(observer, NEXT)
    run(ledger, observer, NEXT, budget=0)
    calls = []
    result = run(ledger, observer, NEXT, research_fn=fake_research({}, calls))
    assert result["mark"] == "ALREADY_MARKED"
    assert result["model_attempts"] == 3 and calls == NAMES[:3]

    before = ledger.read_bytes()
    again = run(
        ledger,
        observer,
        NEXT,
        research_fn=lambda *a, **k: pytest.fail("duplicate model attempt"),
    )
    assert again["model_attempts"] == 0
    assert ledger.read_bytes() == before


def test_unchanged_sources_resume_remaining_attempts_after_interruption(account):
    ledger, observer = account
    sources(observer, NEXT)
    calls = []

    def interrupted(*args, **kwargs):
        calls.append(kwargs["ticker"])
        raise KeyboardInterrupt("interrupted fake research")

    with pytest.raises(KeyboardInterrupt):
        run(ledger, observer, NEXT, research_fn=interrupted)

    result = run(ledger, observer, NEXT, research_fn=fake_research({}, calls))
    assert result["mark"] == "ALREADY_MARKED"
    assert result["model_attempts"] == 2 and result["daily_attempts"] == 3
    assert calls == NAMES[:3]

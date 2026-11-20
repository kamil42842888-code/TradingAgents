"""V3 authoring context stays scoped; existing structured fields retain proof."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.test_autopilot_decision import PM, TRADER, plan
from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
from tradingagents.agents.schemas import PortfolioDecision, TraderProposal
from tradingagents.agents.trader.trader import create_trader
from tradingagents.dataflows.config import run_config, run_config_context
from tradingagents.paper.autopilot.execution_mandate import (
    BLOCK_START,
    CONFIG_KEY,
    V3_MODE,
    execution_mandate,
    read_execution_terms,
)


def state():
    return {
        "company_of_interest": "NVDA",
        "trade_date": "2026-10-21",
        "instrument_context": "Verified USD ordinary stock.",
        "portfolio_context": "Known holdings: none. Cash is not supplied.",
        "market_report": "Confirmed close 225; technical breakout 226.87; support 212.16.",
        "investment_plan": "Positive research view; execution remains conditional.",
        "trader_investment_plan": TRADER,
        "risk_debate_state": {
            "history": "Weigh evidence and actual execution risk.",
            "aggressive_history": "", "conservative_history": "", "neutral_history": "",
            "latest_speaker": "", "current_aggressive_response": "",
            "current_conservative_response": "", "current_neutral_response": "", "count": 0,
        },
    }


def node_call(role, output=None):
    llm = MagicMock()
    if output is None:
        output = (
            TraderProposal(action="Hold", reasoning="NO_TRADE: evidence incomplete.")
            if role == "trader"
            else PortfolioDecision(rating="Hold", executive_summary="No entry.", investment_thesis="Incomplete evidence.")
        )
    llm.with_structured_output.return_value.invoke.return_value = output
    factory = create_trader if role == "trader" else create_portfolio_manager
    result = factory(llm)(state())
    prompt = llm.with_structured_output.return_value.invoke.call_args.args[0]
    text = prompt if isinstance(prompt, str) else "\n".join(message["content"] for message in prompt)
    return text, result


@pytest.mark.parametrize("role", ["trader", "portfolio_manager"])
def test_only_v3_nodes_receive_the_execution_mandate(role):
    with run_config({}):
        original, _ = node_call(role)
        assert "V3 CONDITIONAL PAPER EXECUTION MANDATE" not in original
        with run_config({CONFIG_KEY: V3_MODE}):
            active, _ = node_call(role)
            assert "V3 CONDITIONAL PAPER EXECUTION MANDATE" in active
            for instruction in (
                "NO_TRADE", "consecutive completed daily closes", "future closes",
                "not a multiple of an undefined standard allocation",
                "Do not invent holdings, cash", "1-21 calendar days", "2-60 trading sessions",
            ):
                assert instruction in active
        after, _ = node_call(role)
        assert after == original


def test_interleaved_run_context_does_not_leak_v3_into_general_research():
    v3 = run_config_context({CONFIG_KEY: V3_MODE})
    general = run_config_context({})
    assert v3.run(execution_mandate, "trader")
    assert general.run(execution_mandate, "trader") == ""
    assert v3.run(execution_mandate, "portfolio_manager")
    assert general.run(execution_mandate, "portfolio_manager") == ""


def test_unknown_mode_fails_closed_instead_of_using_the_wrong_mandate():
    with run_config({CONFIG_KEY: "future-unsupported-version"}), pytest.raises(ValueError, match="Unknown paper execution"):
        execution_mandate("trader")


def test_canonical_execution_contract_survives_actual_structured_pm_rendering():
    output = PortfolioDecision(
        rating="Overweight", executive_summary=PM,
        investment_thesis="The dated price structure supports a limited conditional plan.",
        time_horizon="20 trading sessions after a fill",
    )
    with run_config({CONFIG_KEY: V3_MODE, "output_language": "French"}):
        prompt, result = node_call("portfolio_manager", output)
    text = result["final_trade_decision"]
    assert "\n" + BLOCK_START + "\n" in text
    terms = read_execution_terms(text)
    assert terms["allocation_fraction"] == Decimal("0.05")
    assert terms["holding_sessions"] == 20
    accepted = plan(pm=text)
    assert accepted.required_closes == 2
    assert accepted.max_holding_sessions == 20
    assert "Start executive_summary with explanatory prose" in prompt
    assert "canonical close-trigger and executable-stop sentences in English" in prompt
    assert prompt.index("Write your entire response in French") < prompt.index("V3 CONDITIONAL PAPER EXECUTION MANDATE")


def test_v3_graph_builders_opt_in_without_changing_general_defaults(tmp_path, monkeypatch):
    from tradingagents import default_config
    from tradingagents.graph import trading_graph
    from tradingagents.paper.autopilot import daily_research, daily_support, runner

    baseline = dict(default_config.DEFAULT_CONFIG)
    monkeypatch.setattr(default_config, "build_default_config", lambda: dict(baseline))

    def fake_graph(*, config, callbacks=None):
        model = SimpleNamespace(callbacks=callbacks, max_tokens=config.get("max_tokens"), max_retries=0)
        return SimpleNamespace(config=config, deep_thinking_llm=model, quick_thinking_llm=model)

    monkeypatch.setattr(trading_graph, "TradingAgentsGraph", fake_graph)
    original = runner._graph_factory(tmp_path)
    budget = daily_research.RequestBudget(tmp_path / "budget.json", daily_support.DEFAULTS)
    managed = daily_research.guarded_graph(tmp_path, daily_support.DEFAULTS, budget)
    assert original.config[CONFIG_KEY] == managed.config[CONFIG_KEY] == V3_MODE
    assert CONFIG_KEY not in default_config.DEFAULT_CONFIG
    assert CONFIG_KEY not in baseline
    assert managed.config["checkpoint_enabled"] is False
    assert managed.config["llm_max_retries"] == 0
    assert not budget.data["requests"]

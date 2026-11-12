"""No-network subscription routing, durable admissions, and completion checks."""

from types import SimpleNamespace as NS

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from tradingagents import default_config
from tradingagents.llm_clients import chatgpt_auth
from tradingagents.paper.autopilot import daily_plan as plan, daily_support as s
from tradingagents.paper.autopilot.daily_research import (
    BudgetStop,
    RequestBudget,
    guarded_graph,
    model_callback,
)


@pytest.fixture
def approved(tmp_path, monkeypatch):
    monkeypatch.setattr(chatgpt_auth, "AUTH_FILE", tmp_path / "signin.json")
    s.atomic_json(
        chatgpt_auth.AUTH_FILE,
        {
            "account": {"client_id": "test-client", "subject": "test-person"},
            "tokens": {"access_token": "test-only", "scopes": ["chatgpt.tokens.use.direct"]},
        },
    )
    monkeypatch.setattr(chatgpt_auth, "access_token", lambda: "test-only")
    config = dict(default_config.DEFAULT_CONFIG, llm_provider="chatgpt")
    monkeypatch.setattr(default_config, "build_default_config", lambda: dict(config))
    return plan.confirm_config(s.DEFAULTS), config


def test_previous_release_config_remains_readable_without_billing_permission():
    old = {
        k: v
        for k, v in s.DEFAULTS.items()
        if k
        not in {
            "model_billing_mode",
            "chatgpt_credits_disabled_confirmed_at",
            "chatgpt_account_sha256",
        }
    }
    assert s.validate_config(old)["model_billing_mode"] == "provider_api"
    assert "model_billing_mode" not in old
    with pytest.raises(ValueError, match="schema"):
        s.validate_config({**old, "unknown": True})


def test_plan_only_requires_real_confirmation_and_saved_grant(tmp_path, monkeypatch):
    with pytest.raises((ValueError, TypeError)):
        s.validate_config(dict(s.DEFAULTS, model_billing_mode="chatgpt_plan_only"))
    monkeypatch.setattr(chatgpt_auth, "AUTH_FILE", tmp_path / "missing.json")
    with pytest.raises(ValueError, match="Saved ChatGPT"):
        plan.confirm_config(s.DEFAULTS)


@pytest.mark.parametrize("tier", ["deep", "quick"])
def test_api_provider_override_is_rejected_before_graph_creation(approved, tmp_path, tier):
    cfg, models = approved
    models[tier + "_think_provider"] = "openai"
    budget = RequestBudget(tmp_path / "budget.json", cfg)
    with pytest.raises(ValueError, match="refuses API-key"):
        guarded_graph(tmp_path, cfg, budget)
    assert not budget.data["requests"]


def test_endpoint_override_cannot_receive_plan_token(approved):
    cfg, models = approved
    with pytest.raises(ValueError, match="official OpenAI"):
        plan.check_route(cfg, dict(models, backend_url="https://example.invalid/v1"))


def test_account_switch_is_rejected_before_request(approved, tmp_path):
    cfg, _ = approved
    budget = RequestBudget(tmp_path / "budget.json", cfg)
    changed = s.read_json(chatgpt_auth.AUTH_FILE)
    changed["account"]["subject"] = "different-person"
    s.atomic_json(chatgpt_auth.AUTH_FILE, changed)
    with pytest.raises(ValueError, match="sign-in changed"):
        budget.reserve("one", 1)
    assert not budget.data["requests"]


def test_real_graph_keeps_plan_transport_and_never_uses_api_key(approved, tmp_path, monkeypatch):
    cfg, _ = approved
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    budget = RequestBudget(tmp_path / "budget.json", cfg)
    graph = guarded_graph(tmp_path, cfg, budget)
    for model in (graph.deep_thinking_llm, graph.quick_thinking_llm):
        assert model.openai_api_key() == "test-only"
        assert model.max_tokens is None and model.max_retries == 0
        assert model.streaming and model.store is False
        payload = model._get_request_payload([("user", "Test")])
        assert "max_output_tokens" not in payload
    assert not budget.data["requests"]


def test_plan_reserves_remaining_budget_then_refunds_only_completed_usage(approved, tmp_path):
    cfg, _ = approved
    b = RequestBudget(tmp_path / "budget.json", cfg)
    b.reserve("one", 10)
    assert b.summary()["charged_token_units"] == 250000
    b.finish("one", 1000)
    b.reserve("two", 10)
    assert b.data["requests"]["two"]["reserved_units"] == 249000
    b.finish("two", 2000)
    resumed = RequestBudget(b.path, cfg)
    assert not resumed.blocked and resumed.summary()["charged_token_units"] == 3000
    assert not resumed.summary()["server_output_cap_enforced"]


@pytest.mark.parametrize("interrupted", [False, True])
def test_unknown_or_interrupted_usage_stops_after_restart(approved, tmp_path, interrupted):
    cfg, _ = approved
    b = RequestBudget(tmp_path / "budget.json", cfg)
    b.reserve("one", 10)
    if not interrupted:
        b.finish("one", None)
    resumed = RequestBudget(b.path, cfg)
    with pytest.raises(BudgetStop, match="already stopped"):
        resumed.reserve("two", 10)
    assert resumed.summary()["model_requests"] == 1


def result(status="completed", usage=100):
    meta = (
        None
        if usage is None
        else {"input_tokens": usage, "output_tokens": 0, "total_tokens": usage}
    )
    return LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content="answer",
                        response_metadata={"status": status} if status else {},
                        usage_metadata=meta,
                    )
                )
            ]
        ]
    )


@pytest.mark.parametrize("status", [None, "incomplete", "failed"])
def test_partial_response_cannot_become_a_decision(approved, tmp_path, status):
    cfg, _ = approved
    b = RequestBudget(tmp_path / "budget.json", cfg)
    callback = model_callback(b)
    callback.on_llm_start({}, ["hello"], run_id="one")
    with pytest.raises(BudgetStop, match="completed inference"):
        callback.on_llm_end(result(status), run_id="one")
    assert b.blocked and b.summary()["charged_token_units"] == 250000


def test_completed_stream_usage_is_counted_and_unknown_usage_stops(approved, tmp_path):
    cfg, _ = approved
    b = RequestBudget(tmp_path / "budget.json", cfg)
    callback = model_callback(b)
    callback.on_llm_start({}, ["hello"], run_id="one")
    callback.on_llm_end(result(), run_id="one")
    assert b.summary()["charged_token_units"] == 100
    callback.on_llm_start({}, ["hello"], run_id="two")
    with pytest.raises(BudgetStop, match="usage unknown"):
        callback.on_llm_end(result(usage=None), run_id="two")
    assert b.blocked


def test_reported_overrun_blocks_new_requests_after_restart(approved, tmp_path):
    cfg, _ = approved
    b = RequestBudget(tmp_path / "budget.json", cfg)
    b.reserve("one", 1)
    b.finish("one", 250001)
    with pytest.raises(BudgetStop):
        RequestBudget(b.path, cfg).reserve("two", 1)


def test_plan_activation_failure_never_arms_or_writes_ledger(approved, tmp_path, monkeypatch):
    from tradingagents.paper.autopilot import daily_supervisor as supervisor, state

    _, models = approved
    models["quick_think_provider"] = "openai"
    monkeypatch.setattr(supervisor, "check_integrity", lambda p: None)
    monkeypatch.setattr(
        state,
        "read",
        lambda p: NS(
            auto_paper_enabled=True,
            portfolio=NS(book=NS(last_session="2026-11-02")),
        ),
    )
    with pytest.raises(ValueError, match="refuses API-key"):
        supervisor.arm(tmp_path, root=tmp_path, plan_only=True)
    assert not (tmp_path / "config.json").exists()

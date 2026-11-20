from types import SimpleNamespace

import pytest

from tradingagents.paper.autopilot.decision import (
    AIExecutionProposal,
    policy_intent,
    request_proposal,
)
from tradingagents.paper.autopilot.execution_mandate import BLOCK_END, BLOCK_START


def execution_block(**changes):
    fields = {
        "action": "CONDITIONAL_LONG", "trigger_usd": "226.87", "stop_usd": "212.16",
        "required_closes": 2, "allocation_fraction": "0.05", "expiry_days": 14,
        "holding_sessions": 20,
    }
    fields.update(changes)
    return "\n".join([BLOCK_START, *(f"{key}={value}" for key, value in fields.items()), BLOCK_END])


PM="Rating: Overweight. Initiate after TWO consecutive closes above $226.87. Use a firm stop-loss at $212.16 for the paper trade, and reassess if it breaks.\n" + execution_block()
ENTRY="TWO consecutive closes above $226.87"
TRADER="**Stop Loss**: $212.16"
REPORT="Original PM and trader document."


def proposal(**kw):
    args = {
        "action": "CONDITIONAL_LONG", "rating": "Overweight", "entry_quote": ENTRY,
        "stop_quote": TRADER, "trigger_usd": 226.87, "stop_usd": 212.16, "required_closes": 2,
        "expiry_days": 14, "holding_sessions": 20, "allocation_fraction": .05,
        "rationale": "Only a confirmed breakout permits a capped paper position",
    }
    return AIExecutionProposal(**(args|kw))


def plan(pr=None,pm=PM,trader=TRADER,close=225):
    return policy_intent(pr or proposal(),ticker="NVDA",session="2026-10-21",
         final_pm=pm,trader_plan=trader,report=REPORT,close_usd=close,sector="Technology")


def test_verbatim_evidence_generates_conditional_not_market_order():
    p=plan()
    assert p.entry_rule=="CLOSES_ABOVE"
    assert p.required_closes==2
    assert p.trigger_usd==226.87
    assert p.stop_loss_usd==212.16
    assert p.initial_allocation_fraction==.05
    assert p.expiry_date=="2026-11-04"
    assert p.max_entry_gap_fraction<=.025
    assert p.report_sha256


def test_overweight_only_does_not_authorise_trade():
    with pytest.raises(ValueError):
        plan(proposal(action="NO_TRADE"))
    with pytest.raises(ValueError):
        plan(pm="Rating: Overweight, please buy now")
    with pytest.raises(ValueError):
        plan(proposal(stop_quote="Risk review $212.16"))


def test_trader_stop_with_pm_risk_review_is_not_executable():
    pm="Rating: Overweight. Initiate after TWO consecutive closes above $226.87. Treat $212.16 as a risk-review level, not an executable stop.\n" + execution_block()
    with pytest.raises(ValueError,match="stop"):
        plan(pm=pm)


def test_hallucinated_one_of_two_required_closes_rejected():
    for count in (1, 3):
        with pytest.raises(ValueError,match="confirmation"):
            plan(proposal(required_closes=count),
                 pm=PM.replace("required_closes=2", f"required_closes={count}"))


def test_missing_proof_or_numeric_mismatch_rejected():
    with pytest.raises(ValueError,match="verbatim"):
        plan(proposal(entry_quote="Only enter after a breakout"))
    with pytest.raises(ValueError,match="grounded"):
        plan(proposal(trigger_usd=230), pm=PM.replace("trigger_usd=226.87", "trigger_usd=230"))
    with pytest.raises(ValueError,match="stop"):
        plan(proposal(stop_usd=200))


def test_very_distant_trigger_or_allocation_exceeds_v3_cap_rejected():
    with pytest.raises(ValueError,match="stale"):
        plan(close=100)
    with pytest.raises(ValueError,match="allocation"):
        plan(proposal(allocation_fraction=.2), pm=PM.replace("allocation_fraction=0.05", "allocation_fraction=0.2"))
    with pytest.raises(ValueError,match="horizon"):
        plan(proposal(holding_sessions=110), pm=PM.replace("holding_sessions=20", "holding_sessions=110"))


def test_numerical_spoofing_has_no_order_authority():
    with pytest.raises(ValueError):
        plan(proposal(entry_quote="TWO consecutive closes above $226.88"))
    with pytest.raises(ValueError):
        plan(proposal(required_closes=None))


@pytest.mark.parametrize("field,value", [
    ("trigger_usd", 227.0), ("stop_usd", 211.0), ("required_closes", 1),
    ("allocation_fraction", .08), ("expiry_days", 15), ("holding_sessions", 21),
])
def test_extractor_cannot_change_any_original_execution_number(field, value):
    with pytest.raises(ValueError, match=field):
        plan(proposal(**{field: value}))


@pytest.mark.parametrize("pm", [
    PM.split(BLOCK_START)[0],
    PM + "\n" + execution_block(),
    PM.replace("expiry_days=14\n", ""),
    PM.replace("expiry_days=14", "expiry_days=14\nexpiry_days=15"),
    PM.replace("expiry_days=14", "expiry_days=14\nunknown=1"),
    PM.replace("expiry_days=14", "expiry_days=14.0"),
    PM.replace("allocation_fraction=0.05", "allocation_fraction=5%"),
    PM.replace("stop_usd=212.16", "stop_usd=NaN"),
])
def test_missing_or_ambiguous_original_terms_cannot_be_repaired_by_extraction(pm):
    with pytest.raises(ValueError):
        plan(pm=pm)


def test_no_trade_original_action_cannot_be_overridden_or_cost_an_extraction_call():
    pm = f"Rating: Overweight. Missing executable evidence; retain cash.\n{BLOCK_START}\naction=NO_TRADE\n{BLOCK_END}"
    with pytest.raises(ValueError, match="action"):
        plan(pm=pm)
    # No model object is needed: the original explicit refusal settles extraction.
    result = request_proposal(SimpleNamespace(), ticker="NVDA", session="2026-10-21",
                              pm=pm, trader=TRADER)
    assert result.action == "NO_TRADE"
    assert result.trigger_usd is None


@pytest.mark.parametrize("replacement", [
    "Do not initiate after TWO consecutive closes above $226.87.",
    "Hypothetical example: initiate after TWO consecutive closes above $226.87.",
])
def test_negated_or_example_entry_is_not_authority(replacement):
    pm = PM.replace("Initiate after TWO consecutive closes above $226.87.", replacement)
    with pytest.raises(ValueError, match="negated or hypothetical"):
        plan(pm=pm)


def test_entry_count_and_trigger_must_belong_to_the_same_price_phrase():
    entry = "TWO consecutive closes above $230.00; the current price is $226.87"
    with pytest.raises(ValueError, match="grounded"):
        plan(proposal(entry_quote=entry), pm=PM.replace(ENTRY, entry))


@pytest.mark.parametrize("replacement", [
    "Use a firm stop-loss at $200.00 for the paper trade.",
    "Do not use a firm stop-loss at $212.16 for the paper trade.",
    "Never use a firm stop-loss at $212.16 for the paper trade.",
    "A hypothetical firm stop-loss at $212.16 is an example.",
])
def test_stop_price_and_affirmation_must_be_the_same_unnegated_instruction(replacement):
    pm = PM.replace("Use a firm stop-loss at $212.16 for the paper trade, and reassess if it breaks.", replacement)
    with pytest.raises(ValueError, match="stop"):
        plan(pm=pm)


def test_trader_stop_quote_cannot_hide_its_negation():
    with pytest.raises(ValueError, match="stop"):
        plan(trader="Do not use " + TRADER)


def test_trader_stop_price_cannot_be_borrowed_from_an_unrelated_number():
    quote = "Use an executable stop-loss at $200.00; current price $212.16"
    with pytest.raises(ValueError, match="stop"):
        plan(proposal(stop_quote=quote), trader=quote)


@pytest.mark.parametrize("horizon", ["3-6 months", "21 trading sessions after a fill", "not provided"])
def test_rendered_pm_holding_horizon_must_agree_with_the_authored_block(horizon):
    with pytest.raises(ValueError, match="horizon"):
        plan(pm=PM + "\n**Time Horizon**: " + horizon)


def test_subcent_price_difference_is_not_exact_source_agreement():
    pm = PM.replace("above $226.87.", "above $226.874.")
    with pytest.raises(ValueError, match="grounded"):
        plan(proposal(entry_quote=ENTRY + "4"), pm=pm)

"""V3-only authoring instructions and the original PM execution contract.

The normal research schemas remain prose-oriented. V3 asks their existing text
fields to carry an explicit contract before a separate model extracts evidence.
That extraction may copy the original decision, never choose missing terms.
"""

from __future__ import annotations

import re
from decimal import Decimal

CONFIG_KEY = "paper_execution_mode"
V3_MODE = "koral_v3_conditional_long_v1"
BLOCK_START = "KORAL_V3_EXECUTION_V1"
BLOCK_END = "END_KORAL_V3_EXECUTION"
FLOAT_FIELDS = ("trigger_usd", "stop_usd", "allocation_fraction")
INTEGER_FIELDS = ("required_closes", "expiry_days", "holding_sessions")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_INTEGER = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def read_execution_terms(pm: str) -> dict:
    """Read exactly one unambiguous PM-authored block; do not infer defaults."""
    lines = [line.strip() for line in pm.splitlines()]
    if lines.count(BLOCK_START) != 1 or lines.count(BLOCK_END) != 1:
        raise ValueError("Exactly one original PM V3 execution block is required")
    start, end = lines.index(BLOCK_START), lines.index(BLOCK_END)
    if end <= start:
        raise ValueError("Invalid original PM V3 execution block order")
    terms = {}
    for line in lines[start + 1:end]:
        if not line:
            continue
        key, separator, value = line.partition("=")
        if not separator or key in terms or key != key.strip() or value != value.strip():
            raise ValueError("Ambiguous original PM V3 execution field")
        terms[key] = value
    if terms.get("action") == "NO_TRADE":
        if set(terms) != {"action"}:
            raise ValueError("Original PM NO_TRADE must not contain entry terms")
        return terms
    expected = {"action", *FLOAT_FIELDS, *INTEGER_FIELDS}
    if terms.get("action") != "CONDITIONAL_LONG" or set(terms) != expected:
        raise ValueError("Original PM conditional execution terms are incomplete or unknown")
    for key in FLOAT_FIELDS:
        if not _DECIMAL.fullmatch(terms[key]) or len(terms[key]) > 32:
            raise ValueError(f"Invalid original PM numeric execution term: {key}")
        terms[key] = Decimal(terms[key])
    for key in INTEGER_FIELDS:
        if not _INTEGER.fullmatch(terms[key]) or len(terms[key]) > 3:
            raise ValueError(f"Invalid original PM integer execution term: {key}")
        terms[key] = int(terms[key])
    return terms


def execution_mandate(role: str) -> str:
    """Opt in only inside a V3 graph's existing run-scoped configuration."""
    from tradingagents.dataflows.config import get_config

    mode = get_config().get(CONFIG_KEY)
    if mode in (None, ""):
        return ""
    if mode != V3_MODE:
        raise ValueError("Unknown paper execution mandate")
    if role not in {"trader", "portfolio_manager"}:
        raise ValueError("Unknown paper execution author")
    shared = (
        "\n\nV3 CONDITIONAL PAPER EXECUTION MANDATE\n"
        "This run may author future long-only simulated entries; it never sends broker orders. "
        "Keep the evidence-based rating independent of execution eligibility; never force a Buy "
        "or Overweight to create activity. If the evidence cannot support a complete plan, "
        "explicitly choose NO_TRADE and explain the missing requirement. Unavailable news or "
        "sentiment is missing evidence, not observed neutrality.\n"
        "Author a conditional plan from the dated technical evidence and risk analysis, not "
        "from the rating alone. Only entry after 1, 2, or 3 consecutive completed daily closes "
        "above a stated USD trigger is supported. State the count explicitly. Only future "
        "closes after this decision may confirm it; do not claim a fill or count earlier "
        "closes retrospectively. The engine may later simulate a next-session opening fill "
        "if its independent calendar, price, gap, exposure and account checks pass.\n"
        "Choose an explicit executable protective stop below the trigger and no more than "
        "30% below it, grounded in the technical price structure. A monitoring, reassessment, "
        "tentative or risk-review level is not an executable stop. Stops can gap and do not "
        "guarantee a fill price. The trigger must be within 75%-120% of the confirmed close.\n"
        "Specify an initial allocation fraction greater than 0 and at most 0.10 of total "
        "paper-account equity, not a multiple of an undefined standard allocation. Do not "
        "invent holdings, cash, buying power or currency conversions; only supplied holdings "
        "are known, and the engine checks GBP equity and available cash independently. "
        "Specify entry expiry as 1-21 calendar days after the decision date and maximum "
        "holding as 2-60 trading sessions after a fill. These are original risk decisions "
        "you must justify, never defaults for a later extractor to invent.\n"
    )
    if role == "trader":
        return shared + (
            "In the existing reasoning/position_sizing text, state the exact close trigger, "
            "confirmation count, allocation fraction, expiry days and holding sessions; "
            "keep entry_price and stop_loss as the same numeric USD levels. State clearly "
            "that the stop is executable if you recommend it. If no complete supported "
            "plan is justified, write NO_TRADE in the reasoning, leave entry/stop absent, "
            "and do not treat an unowned stock as a sellable holding."
        )
    return shared + (
        "Use your existing executive_summary text field for the final execution decision. "
        "A conditional long requires Buy/Overweight and your explicit affirmation of the "
        "same protective stop price stated by the Trader. Do not silently substitute a "
        "different stop. State the entry as '<count> consecutive closes above $<trigger>' "
        "and affirm 'Use a firm executable stop-loss at $<stop>'. Include the numeric "
        "allocation, expiry and holding choices in your explanation, agreeing with the "
        "following block. For a conditional long, set the existing time_horizon field "
        "exactly to '<holding_sessions> trading sessions after a fill', agreeing with "
        "the block rather than the generic schema's multi-month example. Keep its labels, "
        "values, the time_horizon field and the canonical close-trigger and "
        "executable-stop sentences in English even in a translated report. Put those "
        "affirmative instructions in their own sentences; put cautions and alternatives "
        "in separate sentences. Start executive_summary with explanatory prose, then a "
        "newline before the block marker so the report's Executive Summary label cannot "
        "share its line. Place exactly one block on its own lines inside executive_summary; replace every "
        "placeholder with your actual authored number, without units, percentages or ranges:\n"
        f"{BLOCK_START}\n"
        "action=CONDITIONAL_LONG\n"
        "trigger_usd=<USD trigger>\n"
        "stop_usd=<USD executable stop>\n"
        "required_closes=<integer 1-3>\n"
        "allocation_fraction=<decimal greater than 0 and at most 0.10>\n"
        "expiry_days=<integer 1-21>\n"
        "holding_sessions=<integer 2-60>\n"
        f"{BLOCK_END}\n"
        "If you decline execution, omit every numeric term and use only:\n"
        f"{BLOCK_START}\naction=NO_TRADE\n{BLOCK_END}\n"
        "A positive research rating may still have NO_TRADE. Do not describe an immediate "
        "limit/market purchase or a multi-month compounder as this conditional swing contract."
    )

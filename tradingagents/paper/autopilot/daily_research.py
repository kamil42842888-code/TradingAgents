"""Bounded multi-company paper research; no rating-only orders or short entries.

A company limit is not a cash-price cap. Request admissions reserve conservative
prompt-byte/output allowances, refunding only trustworthy reported token usage.
Rates/invoices remain provider-dependent; unknown usage remains fully reserved.
"""

from __future__ import annotations

import threading
from copy import deepcopy
from pathlib import Path

from . import daily_support as support


class BudgetStop(RuntimeError):
    pass


class RequestBudget:
    """Persistent request/token admission ledger, protected by caller's paper lock."""

    def __init__(self, path: Path, config: dict):
        self.path = path
        self.lock = threading.RLock()
        self.blocked = False
        self.policy = {
            k: config[k]
            for k in (
                "max_model_requests_per_session",
                "max_reserved_tokens_per_session",
                "max_output_tokens_per_request",
                "max_prompt_bytes_per_request",
            )
        }
        if path.exists():
            self.data = support.read_json(path)
            if (
                self.data.get("schema") != "koral-request-budget-v1"
                or self.data.get("policy") != self.policy
            ):
                raise BudgetStop("Daily request limits cannot change after first reservation")
        else:
            self.data = {
                "schema": "koral-request-budget-v1",
                "policy": self.policy,
                "requests": {},
                "note": "NOT a guaranteed monetary invoice ceiling",
            }
            # Establish the durable ledger before any company STARTED receipt.
            support.atomic_json(self.path, self.data)
        if not isinstance(self.data.get("requests"), dict):
            raise BudgetStop("Invalid persisted request ledger")
        for record in self.data["requests"].values():
            if (
                not isinstance(record, dict)
                or type(record.get("charged_units")) is not int
                or record["charged_units"] < 0
            ):
                raise BudgetStop("Invalid persisted request reservation")
            if record.get("reservation_exceeded"):
                self.blocked = True

    def reserve(self, run_id: str, prompt_bytes: int):
        with self.lock:
            key = str(run_id)
            if self.blocked:
                raise BudgetStop("Daily model budget is already stopped")
            if key in self.data["requests"]:
                return
            records = self.data["requests"]
            # UTF-8 bytes are a conservative proxy, not exact tokenisation.
            # Include framing/headroom and the configured output cap.
            units = prompt_bytes + self.policy["max_output_tokens_per_request"] + 8192
            used = sum(r["charged_units"] for r in records.values())
            if (
                prompt_bytes > self.policy["max_prompt_bytes_per_request"]
                or len(records) >= self.policy["max_model_requests_per_session"]
                or used + units > self.policy["max_reserved_tokens_per_session"]
            ):
                self.blocked = True
                raise BudgetStop("Daily model request/token admission limit reached")
            records[key] = {
                "charged_units": units,
                "reserved_units": units,
                "status": "STARTED",
                "prompt_bytes": prompt_bytes,
            }
            support.atomic_json(self.path, self.data)  # persisted BEFORE provider request

    def finish(self, run_id: str, usage: int | None):
        with self.lock:
            item = self.data["requests"].get(str(run_id))
            if item is None:
                raise BudgetStop("Unreserved model request completion")
            if type(usage) is int and usage >= 0:
                item["charged_units"] = usage
                item["reported_tokens"] = usage
                item["status"] = "COMPLETE_WITH_USAGE"
                if usage > item["reserved_units"]:
                    self.blocked = True
                    item["reservation_exceeded"] = True
            else:
                item["status"] = "COMPLETE_USAGE_UNKNOWN_RESERVATION_RETAINED"
            support.atomic_json(self.path, self.data)

    def summary(self):
        values = self.data["requests"].values()
        return {
            "model_requests": len(self.data["requests"]),
            "charged_token_units": sum(x["charged_units"] for x in values),
            "budget_stopped": self.blocked,
            "monetary_cap_enforced": False,
        }


def model_callback(budget: RequestBudget):
    from langchain_core.callbacks import BaseCallbackHandler

    class Guard(BaseCallbackHandler):
        raise_error = True
        run_inline = True

        def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
            budget.reserve(str(run_id), len(str(messages).encode("utf-8")))

        def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
            budget.reserve(str(run_id), len(str(prompts).encode("utf-8")))

        def on_llm_end(self, response, *, run_id, **kwargs):
            usage = None
            info = getattr(response, "llm_output", None) or {}
            raw = info.get("token_usage") or info.get("usage") or {}
            if type(raw.get("total_tokens")) is int:
                usage = raw["total_tokens"]
            else:
                totals = []
                for batch in getattr(response, "generations", []) or []:
                    for generation in batch:
                        meta = getattr(getattr(generation, "message", None), "usage_metadata", None)
                        if isinstance(meta, dict) and type(meta.get("total_tokens")) is int:
                            totals.append(meta["total_tokens"])
                if totals:
                    usage = sum(totals)
            budget.finish(str(run_id), usage)

        def on_llm_error(self, error, *, run_id, **kwargs):
            # The request may have been billed. No refund or automatic retry.
            if str(run_id) in budget.data["requests"]:
                budget.finish(str(run_id), None)

    return Guard()


def guarded_graph(root: Path, config: dict, guard: RequestBudget):
    from tradingagents.default_config import build_default_config
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    # Re-read environment after the supervisor loads the project .env; do not
    # copy/import credentials into the deployment package or operation logs.
    cfg = build_default_config()
    cfg["results_dir"] = str(root / "agent_results")
    cfg["memory_log_path"] = str(root / "memory.md")
    cfg["max_tokens"] = config["max_output_tokens_per_request"]
    cfg["llm_max_retries"] = 0
    cfg["checkpoint_enabled"] = False  # durable controller receipt owns retries
    callback = model_callback(guard)
    graph = TradingAgentsGraph(config=cfg, callbacks=[callback])
    for model in (graph.deep_thinking_llm, graph.quick_thinking_llm):
        callbacks = getattr(model, "callbacks", None)
        if not isinstance(callbacks, list) or callback not in callbacks:
            raise BudgetStop("Provider model did not retain request-admission guard")
        cap = getattr(model, "max_tokens", None) or getattr(model, "max_output_tokens", None)
        if (
            cap != config["max_output_tokens_per_request"]
            or getattr(model, "max_retries", None) != 0
        ):
            raise BudgetStop("Provider did not retain bounded output and zero SDK retries")
    return graph


def started_tickers(state, session: str) -> set[str]:
    return {
        e.get("details", {}).get("ticker")
        for e in state.audit
        if e.get("kind") == "AI_RESEARCH_STARTED" and e.get("details", {}).get("date") == session
    }


def research_session(
    ledger: Path,
    session: str,
    names: list[str],
    source: dict,
    *,
    max_companies: int,
    graph_factory=None,
    research_fn=None,
    completion_clock=None,
    config=None,
) -> dict:
    """Caller holds coordinated account lock; admitted slate is fixed before results."""
    from tradingagents.paper.autopilot import paper_forward, runner
    from tradingagents.paper.autopilot.state import atomic_write, read
    from tradingagents.paper.conditional import TradeIntent

    if type(max_companies) is not int or not 0 <= max_companies <= 3:
        raise ValueError("Daily company budget must be 0-3")
    st = read(ledger)
    if not st.can_buy():
        return {"research": "PAPER_DISABLED_OR_RISK_HALTED", "model_attempts": 0}
    if st.portfolio.book.last_session != session:
        raise ValueError("Research must follow current source-bound session mark")
    config = support.validate_config(config or support.load_config())
    clock = completion_clock or support.utcnow
    starts = started_tickers(st, session)
    if len(starts) >= max_companies or max_companies == 0:
        return {"research": "DAILY_AI_BUDGET_ALREADY_USED", "model_attempts": 0}
    slate_events = [
        e
        for e in st.audit
        if e.get("kind") == "DAILY_RESEARCH_SLATE" and e.get("details", {}).get("date") == session
    ]
    if slate_events:
        detail = slate_events[-1]["details"]
        if (
            detail["daily_company_limit"] != max_companies
            or detail["market_sha256"] != source["market_sha256"]
        ):
            raise ValueError("Daily research slate or budget cannot be revised after declaration")
        candidates = detail["candidates"]
    else:
        active = support.active_symbols(st)
        candidates = [
            t
            for t in names
            if source["tickers"][t]["eligible_for_research"]
            and t not in active
            and t not in starts
            and f"{session}:{t}" not in st.ai_receipts
        ][: max_companies - len(starts)]
        st.event(
            "DAILY_RESEARCH_SLATE",
            date=session,
            candidates=candidates,
            daily_company_limit=max_companies,
            earlier_attempts=len(starts),
            market_sha256=source["market_sha256"],
            ranking="PREDECLARED_ROSTER_ORDER",
        )
        atomic_write(ledger, st)
    budget_path = ledger.parent / "model_budgets" / (session + ".json")
    earlier = [
        e["details"].get("request_budget", {})
        for e in st.audit
        if e["kind"] == "AI_RESEARCH_RECEIPT" and e["details"].get("date") == session
    ]
    if not budget_path.exists() and starts:
        raise BudgetStop("Persisted request ledger missing after paid attempts; reconcile")
    budget = RequestBudget(budget_path, config)
    if any(b.get("model_requests", 0) > budget.summary()["model_requests"] for b in earlier):
        raise BudgetStop("Persisted request ledger regressed; reconcile")
    results = []
    for ticker in candidates:
        st = read(ledger)
        starts = started_tickers(st, session)
        key = f"{session}:{ticker}"
        if len(starts) >= max_companies or budget.blocked or not st.can_buy():
            break
        if ticker in starts or key in st.ai_receipts or ticker in support.active_symbols(st):
            continue
        if support.utc(clock()).astimezone(support.NY).date().isoformat() != session:
            break  # Do not start a billable request in the next NY session.
        st.ai_receipts[key] = {
            "decision": "NO_TRADE",
            "session": session,
            "reason": "AI_ATTEMPT_STARTED_NO_AUTOMATIC_RETRY",
            "status": "IN_PROGRESS",
            "ticker": ticker,
        }
        st.event(
            "AI_RESEARCH_STARTED",
            date=session,
            ticker=ticker,
            model_budget=max_companies,
            source_sha256=source["market_sha256"],
        )
        atomic_write(ledger, st)
        plan = None
        report = None
        outcome = {"decision": "NO_TRADE", "session": session}
        try:
            graph = (
                graph_factory(ledger.parent)
                if graph_factory
                else guarded_graph(ledger.parent, config, budget)
            )
            receipt, plan, report = (research_fn or runner._research_one)(
                graph,
                ticker=ticker,
                session=session,
                bar=source["bars"][ticker],
                root=ledger.parent,
                state=st,
            )
            if not isinstance(receipt, dict):
                raise ValueError("Invalid model receipt")
            paper_forward._verify_report(ledger.parent, ticker, session, receipt, report)
            outcome = dict(receipt)
            if support.utc(clock()).astimezone(support.NY).date().isoformat() != session:
                raise ValueError("AI finished after the original NY session; no new intent")
            if budget.blocked:
                raise BudgetStop("Model admission/usage budget stopped the research")
            if plan is not None:
                if type(plan) is not TradeIntent or plan.side != "ENTER_LONG":
                    raise ValueError("Only original, source-grounded long TradeIntent supported")
                plan.validate()
                st = read(ledger)
                accepted_today = any(
                    e["kind"] == "V3_POLICY_ACCEPTED" and e["details"].get("date") == session
                    for e in st.audit
                )
                if accepted_today:
                    plan = None
                    outcome["decision"] = "NO_TRADE"
                    outcome["reason"] = "VALID_RESEARCH_DAILY_NEW_INTENT_LIMIT"
                else:
                    candidate_state = deepcopy(st)
                    candidate_state.machine_accept(plan, report, session, "POLICY_VERIFIED")
                    outcome["reason"] = "POLICY_VALIDATED_WATCHING_NO_ORDER_YET"
        except Exception as exc:
            plan = None
            outcome = {
                "decision": "NO_TRADE",
                "session": session,
                "reason": "AI_OR_POLICY_FAILED_" + type(exc).__name__,
                "error_detail": str(exc)[:400],
                "research_complete": False,
            }
        st = read(ledger)
        if plan is not None:
            st.machine_accept(plan, report, session, "POLICY_VERIFIED")
        st.ai_receipts[key] = {**outcome, "status": "COMPLETE", "ticker": ticker}
        st.event(
            "AI_RESEARCH_RECEIPT",
            date=session,
            ticker=ticker,
            receipt=outcome,
            source_sha256=source["market_sha256"],
            request_budget=budget.summary(),
        )
        atomic_write(ledger, st)
        results.append(
            {
                "ticker": ticker,
                "decision": outcome.get("decision"),
                "rating": outcome.get("rating"),
                "reason": outcome.get("reason"),
            }
        )
    result = {
        "research": "DAILY_RESEARCH_RECORDED" if results else "NO_NEW_ELIGIBLE_RESEARCH",
        "model_attempts": len(results),
        "results": results,
        "daily_attempts": len(started_tickers(read(ledger), session)),
        "conditional_intents": sum(
            r["status"] == "WATCHING" for r in read(ledger).portfolio.intents.values()
        ),
        "request_budget": budget.summary(),
    }
    if len(results) == 1:
        result.update(results[0])
    return result

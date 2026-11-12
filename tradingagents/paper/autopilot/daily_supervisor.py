"""Scheduled long-only paper workflow. No account reset, enabling, or broker calls.

The existing Observer still captures sources. This separate supervisor freezes
future rosters, waits for real current-session evidence, values the paper book,
and requests at most the predeclared daily company budget. Model/provider
failures are durable decisions, not reasons to retry for a BUY.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, time
from pathlib import Path

from . import daily_roster, daily_support as s


def due_phase(now: datetime) -> tuple[str, str]:
    local = s.utc(now).astimezone(s.NY)
    day = local.date().isoformat()
    if not s.is_session(day):
        return day, "NON_SESSION"
    tm = local.time()
    windows = s.session_windows(day)
    if tm < time(8, 45):
        return day, "ROSTER_PREPARATION"
    if time(8, 45) <= tm <= time(9, 25):
        return day, "PREOPEN_COLLECTION"
    if windows["preclose"][0] <= tm <= windows["preclose"][1]:
        return day, "PRECLOSE_COLLECTION"
    if tm >= windows["close"][0]:
        return day, "CLOSE_PROCESSING"
    return day, "WAITING_FOR_OBSERVER"


def source_paths(observer: Path, day: str) -> list[Path]:
    return [
        observer / "calendar" / day / "preopen.json",
        observer / "calendar" / day / "preclose.json",
        observer / "market" / (day + ".json"),
    ]


def check_integrity(project: Path):
    if (project / ".koral-source-transaction.json").exists():
        raise ValueError("Source recovery transaction unfinished; keep tasks disabled")
    manifest = s.read_json(project / "KE_DAILY_SOURCE_MANIFEST.json")
    if manifest.get("schema") != "koral-daily-installed-sources-v1":
        raise ValueError("Missing source-only installation manifest")
    required = {
        "KE_DAILY_PAPER.py",
        "KE_Observer_Scheduler_2026-10-26.py",
        "tradingagents/paper/autopilot/daily_supervisor.py",
        "tradingagents/paper/autopilot/daily_research.py",
        "tradingagents/paper/autopilot/paper_forward.py",
        "tradingagents/paper/autopilot/state.py",
        "tradingagents/paper/autopilot/daily_sessions.json",
    }
    if not required.issubset(manifest.get("sha256", {})):
        raise ValueError("Incomplete installed source manifest")
    for rel, expected in manifest["sha256"].items():
        if Path(rel).is_absolute() or ".." in Path(rel).parts or ":" in rel:
            raise ValueError("Invalid installed manifest path")
        path = project / rel
        if path.is_symlink() or not path.is_file() or s.sha(path.read_bytes()) != expected:
            raise ValueError("Installed daily source changed: " + rel)


def scan_for_next_session(project: Path, session: str, root: Path, *, runner=None):
    cfg = s.load_config(root)
    marker = root / "scans" / (session + ".json")
    if marker.exists():
        return s.read_json(marker)
    queue = Path.home() / ".tradingagents" / "swing_research" / "runs" / session / "queue.json"
    if queue.exists():
        record = {"status": "EXISTING_SCAN_PRESERVED", "session": session}
        s.atomic_json(marker, record)
        return record
    record = {
        "status": "SCAN_STARTED_NO_AUTOMATIC_RETRY",
        "session": session,
        "utc": s.utcnow().isoformat(),
    }
    s.atomic_json(marker, record)
    cmd = [
        sys.executable,
        "-m",
        "tradingagents.paper.swing_scan",
        "--max-symbols",
        str(cfg["scan_max_symbols"]),
        "--max-profiles",
        str(cfg["scan_max_profiles"]),
        "--max-earnings-checks",
        str(cfg["scan_max_earnings_checks"]),
        "--top",
        "8",
    ]
    log = root / "logs" / (session + "_scan.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log.open("a", encoding="utf-8") as f:
            proc = (runner or subprocess.run)(
                cmd, cwd=project, stdout=f, stderr=subprocess.STDOUT, timeout=900, check=False
            )
        record["status"] = (
            "SCAN_COMPLETED"
            if proc.returncode == 0 and queue.is_file()
            else "SCAN_FAILED_CARRY_CURRENT_ROSTER"
        )
        record["returncode"] = proc.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        record["status"] = "SCAN_FAILED_CARRY_CURRENT_ROSTER"
        record["error"] = type(exc).__name__
    s.atomic_json(marker, record)
    return record


def status(project: Path, root: Path | None = None) -> dict:
    root = root or s.home()
    cfg = s.load_config(root)
    heart = root / "heartbeat.json"
    result = {
        "schema": s.SCHEMA,
        "configuration": cfg,
        "heartbeat": s.read_json(heart) if heart.exists() else None,
        "account_changes": 0,
        "broker_connected": False,
    }
    latest = sorted((root / "summaries").glob("*.json")) if (root / "summaries").exists() else []
    result["latest_session"] = s.read_json(latest[-1]) if latest else None
    return result


def _tick(
    project: Path,
    *,
    now: datetime | None = None,
    root: Path | None = None,
    ledger: Path | None = None,
    observer: Path | None = None,
    probe: bool = False,
    verify_sources: bool = True,
    scan_runner=None,
) -> dict:
    root = root or s.home()
    instant = s.utc(now or s.utcnow())
    day, phase = due_phase(instant)
    base = {
        "schema": s.SCHEMA,
        "utc": instant.isoformat(),
        "ny_session": day,
        "phase": phase,
        "broker_connected": False,
        "paper_only": True,
        "probe": probe,
    }
    if verify_sources:
        check_integrity(project)
    cfg = s.load_config(root)
    with s.os_lock(root / ".supervisor.lock"):
        if probe:
            base["status"] = "SCHEDULED_PROBE_COMPLETED_NO_MODELS_NO_ACCOUNT_WRITES"
            s.atomic_json(root / "probe.json", base)
            return base
        if not cfg["armed"]:
            base["status"] = "SUPERVISOR_NOT_ARMED"
            if cfg["managed_observer"] and cfg["effective_session"]:
                from tradingagents.paper.autopilot.state import STATE

                target = day if phase == "ROSTER_PREPARATION" else s.next_session(day)
                if target < cfg["effective_session"]:
                    target = cfg["effective_session"]
                daily_roster.freeze(ledger or STATE, target, instant, root=root)
                base["status"] = "ROSTER_MAINTENANCE_PAPER_DISARMED"
            s.atomic_json(root / "heartbeat.json", base)
            return base
        from tradingagents.paper.autopilot import paper_bridge, paper_forward
        from tradingagents.paper.autopilot.observe import DEFAULT_OBSERVATION_HOME
        from tradingagents.paper.autopilot.state import STATE, read

        ledger, observer = ledger or STATE, observer or DEFAULT_OBSERVATION_HOME
        # Finish any prepared mark before roster adoption can mutate its ledger.
        from .daily_transaction import recover_pending

        with s.ledger_lock(ledger):
            recover_pending(ledger)
        state = read(ledger)
        # Never auto-init, reset, enable, unpause or clear a drawdown halt.
        if state.portfolio.book.last_session is None:
            raise ValueError(
                "Existing forward baseline required; no automatic account initialisation"
            )
        if (
            s.is_session(day)
            and s.active_symbols(state)
            and s.between(state.portfolio.book.last_session, day)
        ):
            raise ValueError("Unobserved sessions with active exposure require reconciliation")
        if not cfg["effective_session"]:
            raise ValueError("Armed supervisor lacks declared effective session")
        if day < cfg["effective_session"]:
            daily_roster.freeze(ledger, cfg["effective_session"], instant, root=root)
            base["status"] = "WAITING_FOR_DECLARED_FIRST_SESSION"
        elif phase == "NON_SESSION":
            daily_roster.freeze(ledger, s.next_session(day), instant, root=root)
            base["status"] = "NON_SESSION_NO_MODEL_CALLS"
        elif phase == "ROSTER_PREPARATION":
            record = daily_roster.freeze(ledger, day, instant, root=root)
            base.update(status="DAILY_ROSTER_FROZEN", roster=record["watchlist"])
        else:
            record_file = root / "rosters" / (day + ".json")
            if not record_file.exists():
                # A machine starting after the declaration cutoff cannot
                # manufacture a pre-market approved universe.
                daily_roster.freeze(ledger, s.next_session(day), instant, root=root)
                base["status"] = "TODAY_ROSTER_MISSED_NEXT_SESSION_PREPARED"
            else:
                record = daily_roster.validate(s.read_json(record_file), day)
                base["roster"] = record["watchlist"]
                paths = source_paths(observer, day)
                missing = [str(p.relative_to(observer)) for p in paths if not p.is_file()]
                base["missing_sources"] = missing
                if phase != "CLOSE_PROCESSING" or missing:
                    base["status"] = "WAITING_FOR_REQUIRED_OBSERVATIONS"
                else:
                    # Validate before adopting a new watchlist. Preserve every
                    # active symbol. Frozen selection is not trade permission.
                    paper_bridge.inspect_sources(observer, day, record["watchlist"], instant)
                    daily_roster.adopt(ledger, day, record, root=root)
                    cfg_for_research = dict(cfg)
                    output = paper_forward.run_once(
                        ledger,
                        observer,
                        day,
                        record["watchlist"],
                        instant,
                        ai_budget=cfg["max_companies_per_session"],
                        config=cfg_for_research,
                    )
                    state = read(ledger)
                    summary_file = root / "summaries" / (day + ".json")
                    prior = s.read_json(summary_file) if summary_file.exists() else None
                    # Idempotent polls must not erase the day's original AI outcomes.
                    receipts = {
                        k.split(":", 1)[1]: v
                        for k, v in state.ai_receipts.items()
                        if k.startswith(day + ":")
                    }
                    summary = {
                        **base,
                        "status": "FORWARD_SESSION_PROCESSED",
                        "last_execution": output,
                        "research_receipts": receipts,
                        "last_mark": state.portfolio.book.last_session,
                        "equity_gbp": state.portfolio.book.equity(),
                        "cash_gbp": state.portfolio.book.cash_gbp,
                        "conditional_plans": state.portfolio.intents,
                        "positions": sorted(state.portfolio.book.positions),
                        "orders": state.portfolio.book.orders,
                        "active_symbols": sorted(s.active_symbols(state)),
                        "first_processed_utc": prior.get("first_processed_utc")
                        if prior
                        else s.utcnow().isoformat(),
                        "monetary_invoice_cap": False,
                    }
                    s.atomic_json(summary_file, summary)
                    base.update(status="FORWARD_SESSION_PROCESSED", summary=str(summary_file))
                    if (
                        cfg["nightly_scan"]
                        and s.utcnow().astimezone(s.NY).date().isoformat() == day
                    ):
                        base["scan"] = scan_for_next_session(project, day, root, runner=scan_runner)
                    # Next roster uses newly finished scan plus live exposure,
                    # or carries current names if no valid new scan exists.
                    daily_roster.freeze(ledger, s.next_session(day), s.utcnow(), root=root)
        s.atomic_json(root / "heartbeat.json", base)
        return base


def tick(project: Path, **kwargs) -> dict:
    """Persist incomplete-data/error summaries as well as successful sessions."""
    root = kwargs.get("root") or s.home()
    instant = s.utc(kwargs.get("now") or s.utcnow())
    day = instant.astimezone(s.NY).date().isoformat()
    try:
        result = _tick(project, **kwargs)
    except Exception as exc:
        result = {
            "status": "DAILY_CONTROLLER_FAILED_CLOSED",
            "ny_session": day,
            "utc": instant.isoformat(),
            "error_type": type(exc).__name__,
            "message": str(exc)[:800],
            "paper_only": True,
            "broker_connected": False,
        }
        s.atomic_json(root / "heartbeat.json", result)
        s.atomic_json(root / "summaries" / (day + "_error.json"), result)
        raise
    if not kwargs.get("probe") and s.load_config(root)["armed"]:
        path = root / "summaries" / (day + ".json")
        summary = s.read_json(path) if path.exists() else {}
        summary.update(result)
        summary["data_complete"] = (
            not result.get("missing_sources") and result["status"] == "FORWARD_SESSION_PROCESSED"
        )
        from .state import STATE, read

        state = read(kwargs.get("ledger") or STATE)
        summary.update(
            cash_gbp=state.portfolio.book.cash_gbp,
            equity_gbp=state.portfolio.book.equity(),
            positions=state.portfolio.book.positions,
            orders=state.portfolio.book.orders,
            conditional_plans=state.portfolio.intents,
            research_receipts={
                k: v for k, v in state.ai_receipts.items() if k.startswith(day + ":")
            },
        )
        budget_path = (kwargs.get("ledger") or STATE).parent / "model_budgets" / (day + ".json")
        summary["model_usage"] = s.read_json(budget_path) if budget_path.exists() else None
        s.atomic_json(path, summary)
    return result


def arm(
    project: Path, *, now: datetime | None = None, root: Path | None = None, plan_only: bool = False
) -> dict:
    """Enable this schedule only; NEVER toggle the underlying paper account."""
    from tradingagents.paper.autopilot.state import STATE, read

    root = root or s.home()
    check_integrity(project)
    st = read(STATE)
    if not st.auto_paper_enabled or st.portfolio.book.last_session is None:
        raise ValueError(
            "Existing already-enabled paper account and baseline required; no account flags changed"
        )
    instant = s.utc(now or s.utcnow())
    local = instant.astimezone(s.NY)
    day = local.date().isoformat()
    effective = day if s.is_session(day) and local.time() < time(8, 45) else s.next_session(day)
    cfg = s.load_config(root)
    if cfg["armed"]:
        return {"status": "ALREADY_ARMED", "effective_session": cfg["effective_session"]}
    if plan_only:
        import tempfile

        from .daily_plan import confirm_config
        from .daily_research import RequestBudget, guarded_graph

        cfg = confirm_config(cfg)
        # Build both real provider clients without inference or account writes.
        # A route/callback/credential mismatch must fail before the schedule arms.
        with tempfile.TemporaryDirectory(prefix="koral-plan-check-") as scratch:
            directory = Path(scratch)
            guarded_graph(directory, cfg, RequestBudget(directory / "budget.json", cfg))
    else:
        cfg.update(
            model_billing_mode="provider_api",
            chatgpt_credits_disabled_confirmed_at=None,
            chatgpt_account_sha256=None,
        )
    cfg.update(armed=True, managed_observer=True, effective_session=effective)
    s.validate_config(cfg)
    # Freeze before writing the armed configuration; failure never arms half a setup.
    daily_roster.freeze(STATE, effective, instant, root=root)
    s.atomic_json(root / "config.json", cfg)
    return {
        "status": "DAILY_PAPER_SCHEDULE_ARMED",
        "effective_session": effective,
        "max_companies_per_session": cfg["max_companies_per_session"],
        "paper_account_settings_changed": False,
        "shorts_allowed": False,
        "monetary_invoice_cap": False,
        "model_billing_mode": cfg["model_billing_mode"],
        "paid_credits_disabled": "OPERATOR_CONFIRMED_IN_CHATGPT" if plan_only else "NOT_VERIFIED",
    }

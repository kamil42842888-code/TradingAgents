"""Koral Equities: forward-only Observer -> existing V3.1 PAPER executor.

This controller never creates or resets an account and has NO brokerage code.
Only a previously baselined V3.1 paper account may advance. Each real NY
session is validated against immutable Observer preopen/preclose/market files,
then passed into the existing ConditionalBook for its simulated entry, stop,
exit and risk decisions. Historical/missing sessions cannot produce entries.

Real AI research is explicitly budgeted to at most THREE eligible companies per
New York session and requires an already enabled PAPER-ONLY account. Failed model
attempts persist a receipt and are never silently retried on the same day.
Running this once is NOT automatic scheduling; that's a separate release gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from tradingagents.paper.autopilot import paper_bridge

SCHEMA = "koral-forward-paper-one-session-v1"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _next_weekday(value: str) -> str:
    from tradingagents.paper.autopilot.daily_support import next_session

    return next_session(value)


def _snapshot_sources(observer: Path, session: str, tickers: list[str], now: datetime):
    """Use the existing independent, strict as-of evidence reader."""
    from tradingagents.paper.autopilot.state import normalize_tickers

    names = normalize_tickers(tickers, cap=8)
    if not 1 <= len(names) <= 8:
        raise ValueError("Forward pilot requires 1-8 predeclared, observed symbols")
    data, blobs = paper_bridge.inspect_sources(observer, session, names, now)
    return names, data, blobs


def _verify_accepted_reports(ledger: Path, state) -> None:
    """A machine-authorised conditional order is invalid if its report changes.

    This checks accepted model reports on EACH controller invocation, without
    relying solely on the local hash in the original AI receipt.
    """
    import re

    for event in state.audit:
        if event.get("kind") != "V3_POLICY_ACCEPTED":
            continue
        details = event.get("details")
        if not isinstance(details, dict):
            raise ValueError("Accepted AI plan missing provenance")
        session, ticker, digest = (
            details.get("date"),
            details.get("ticker"),
            details.get("report_sha256"),
        )
        if (
            not isinstance(session, str)
            or re.fullmatch(r"\d{4}-\d{2}-\d{2}", session) is None
            or not isinstance(ticker, str)
            or re.fullmatch(r"[A-Z][A-Z0-9.-]{0,11}", ticker) is None
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise ValueError("Accepted AI plan has invalid dated source metadata")
        file = ledger.parent / "reports" / session / ticker / "source.json"
        if file.is_symlink() or not file.is_file() or _sha(file.read_bytes()) != digest:
            raise ValueError(
                "Previously accepted AI report missing or tampered; no paper processing"
            )


def inspect(ledger: Path, observer: Path, session: str, tickers: list[str], now: datetime) -> dict:
    """Read-only gate; reports whether marking or AI is even permissible."""
    from tradingagents.paper.autopilot.state import read

    names, data, blobs = _snapshot_sources(observer, session, tickers, now)
    st = read(ledger)  # verifies bound source archives and ledger hash chain
    _verify_accepted_reports(ledger, st)
    book = st.portfolio.book
    if not any(e.get("kind") == "OBSERVER_SOURCE_BOUND" for e in st.audit):
        raise ValueError(
            "Forward baseline missing; run paper_bridge on a genuine same-day session first"
        )
    if st.watchlist != names:
        raise ValueError(
            "Observer universe differs from paper account; reconcile active holdings/intents before any rotation"
        )
    if book.last_session is None:
        raise ValueError("Paper ledger has no forward baseline")
    if book.last_session > session:
        raise ValueError("Cannot process a day older than the recorded paper book")
    new_session = book.last_session < session
    active = (
        set(book.positions)
        | {o["ticker"] for o in book.orders}
        | {
            x["plan"]["ticker"]
            for x in st.portfolio.intents.values()
            if x["status"] in ("WATCHING", "QUEUED")
        }
    )
    if new_session and _next_weekday(book.last_session) != session and active:
        raise ValueError(
            "Unprocessed sessions with live exposure/intents require reconciliation; no catchup entries"
        )
    if not active.issubset(set(names)):
        raise ValueError("Current observer excludes a held or pending stock; cannot process safely")
    eligible = [ticker for ticker in names if data["tickers"][ticker]["eligible_for_research"]]
    return {
        "schema": SCHEMA,
        "status": "FORWARD_PAPER_INPUT_READY",
        "session": session,
        "roster": names,
        "current_mark": book.last_session,
        "new_session_to_mark": new_session,
        "eligible_for_research": eligible,
        "unverified_research": {
            t: data["tickers"][t]["reason"] for t in names if t not in eligible
        },
        "paper_enabled": st.auto_paper_enabled,
        "paper_paused": st.paused,
        "paper_halted": book.halted,
        "active_symbols": sorted(active),
        "market_sha256": data["market_sha256"],
        "preclose_sha256": _sha(blobs["preclose.json"]),
        "preopen_sha256": _sha(blobs["preopen.json"]),
        "paper_orders_created_by_inspection": 0,
        "broker_connected": False,
    }


def _restore(path: Path, original: bytes) -> None:
    """Durably restore ledger bytes if a writer mutates before raising."""
    fd, tmp = tempfile.mkstemp(prefix=".ke-paper-rollback-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _commit_mark(
    ledger: Path,
    observer: Path,
    session: str,
    names: list[str],
    now: datetime,
    before: dict,
    *,
    write_state=None,
) -> dict:
    """Mark ONLY a fresh completed day; atomic ledger with rollback safety.

    Caller holds the V3.1 exclusive lock. Source clones and market-input archive
    are immutable. An interrupted transaction never changes historical evidence
    or produces a paper entry retroactively.
    """
    from tradingagents.paper.autopilot import runner
    from tradingagents.paper.autopilot.state import atomic_write, read
    from tradingagents.paper.conditional import EarningsSnapshot

    old_bytes = ledger.read_bytes()
    st = read(ledger)
    if st.portfolio.book.last_session == session:
        return {
            "mark": "ALREADY_MARKED",
            "session": session,
            "orders_open": len(st.portfolio.book.orders),
        }
    from tradingagents.paper.autopilot.daily_support import active_symbols

    gap = _next_weekday(st.portfolio.book.last_session) != session
    if st.portfolio.book.last_session > session or (gap and active_symbols(st)):
        raise ValueError("Forward mark cannot bridge missing sessions with active exposure")
    source, blobs = paper_bridge.inspect_sources(observer, session, names, now)
    if (
        before["market_sha256"] != source["market_sha256"]
        or before["preopen_sha256"] != _sha(blobs["preopen.json"])
        or before["preclose_sha256"] != _sha(blobs["preclose.json"])
    ):
        raise ValueError("Observer source changed between preflight and commit")
    calendars = {
        phase: {
            ticker: EarningsSnapshot(**json.loads(blobs[phase + ".json"])["earnings"][ticker])
            for ticker in names
        }
        for phase in ("preopen", "preclose")
    }
    work = deepcopy(st)
    book_before = deepcopy(work.portfolio.book)
    if gap:
        work.event(
            "UNOBSERVED_FLAT_GAP_SKIPPED",
            previous_mark=book_before.last_session,
            next_mark=session,
            notice="Flat account only; no invented intermediate marks, returns or trades",
        )
    if not st.can_buy():
        work.cancel_queued("operator_disabled_or_risk_halt")
    fx0, fx1 = source["fx_gbp_per_usd"]
    bars = source["bars"]
    work.portfolio = work.portfolio.advance(
        session,
        {ticker: bars[ticker] for ticker in names},
        fx0,
        fx1,
        bars["SPY"].close,
        calendars["preopen"],
        calendars["preclose"],
    )
    if work.portfolio.book.last_session != session:
        raise ValueError("Existing conditional engine did not mark the required date")
    # Disabled/paused/risk-halted accounts can still value/manage existing
    # positions, but must not create NEW exposure.
    if not st.can_buy():
        if len(work.portfolio.book.positions) > len(book_before.positions) or any(
            e.get("type") == "BUY_FILLED"
            for e in work.portfolio.book.events[len(book_before.events) :]
        ):
            raise ValueError("Disabled account unexpectedly created new paper exposure")
        work.cancel_queued("operator_disabled_or_risk_halt")
    elif not work.can_buy():
        # A drawdown halt triggered during today's mark cannot carry
        # any pending BUY request forward to another simulated opening.
        work.cancel_queued("operator_disabled_or_risk_halt")

    root = ledger.parent
    copies = {root / "observer_sources" / session / name: raw for name, raw in blobs.items()}
    for phase in ("preopen", "preclose"):
        copies[root / "calendar" / session / (phase + ".json")] = blobs[phase + ".json"]
    archive_file = root / "market_evidence" / (session + ".json")
    if archive_file.exists():
        raise ValueError("Unlinked market evidence file already exists; manual reconciliation")
    # Never remove durable pre-mark backup during rollback.
    backup = (
        root
        / "_bridge_backups"
        / (session + "_forward_" + _sha(old_bytes)[:12])
        / "portfolio_before.json"
    )
    paper_bridge._write_once(backup, old_bytes)
    archive_raw = runner.market_evidence_bytes(
        session=session,
        now=now,
        bars={ticker: bars[ticker] for ticker in names},
        fx_open=fx0,
        fx_close=fx1,
        spy_close=bars["SPY"].close,
    )
    archive_sha = _sha(archive_raw)
    copies[archive_file] = archive_raw
    # Build the entire next state before publishing any of its evidence.
    for phase in ("preopen", "preclose"):
        raw = blobs[phase + ".json"]
        work.event(
            "EARNINGS_SNAPSHOT_CAPTURED",
            session=session,
            phase=phase,
            sha256=_sha(raw),
            imported_from_observer=True,
            original_observed_utc=json.loads(raw)["observed_utc"],
        )
    work.event(
        "OBSERVER_SOURCE_BOUND",
        date=session,
        origin="IMMUTABLE_OBSERVER_SOURCE_NOT_EXCHANGE_CERTIFIED",
        market_sha256=_sha(blobs["market.json"]),
        preopen_sha256=_sha(blobs["preopen.json"]),
        preclose_sha256=_sha(blobs["preclose.json"]),
        paper_market_sha256=archive_sha,
        source_roster=names,
        simulated_orders_after_mark=len(work.portfolio.book.orders),
        simulated_positions_after_mark=len(work.portfolio.book.positions),
    )
    work.event(
        "SESSION_MARKED",
        date=session,
        market_input_sha256=archive_sha,
        backfill=False,
        equity_gbp=round(work.portfolio.book.equity(), 2),
        new_order_count=sum(
            e.get("type") == "V2_CONDITIONAL_ORDER_QUEUED"
            for e in work.portfolio.book.events[len(book_before.events) :]
        ),
        note="Observed forward-session PAPER mark; execution model uses daily OHLC",
    )
    work.event(
        "OBSERVER_FORWARD_SESSION_PROCESSED",
        date=session,
        original_source_sha256=_sha(blobs["market.json"]),
        positions_after=len(work.portfolio.book.positions),
        orders_after=len(work.portfolio.book.orders),
        broker_connected=False,
    )
    if ledger.read_bytes() != old_bytes:
        raise ValueError("Paper account changed during forward mark")
    from . import daily_transaction as transaction
    from .daily_support import canonical

    # Match the existing state writer's native newline translation on Windows.
    after_bytes = canonical(work.to_dict()).replace(b"\n", os.linesep.encode())
    journal = transaction.prepare(ledger, session, old_bytes, after_bytes, copies)
    try:
        transaction.commit(
            ledger, journal, writer=lambda: (write_state or atomic_write)(ledger, work)
        )
    except BaseException:
        transaction.abort(ledger, journal)
        raise
    return {
        "mark": "FORWARD_PAPER_SESSION_MARKED",
        "session": session,
        "positions": len(work.portfolio.book.positions),
        "orders_open": len(work.portfolio.book.orders),
        "equity_gbp": round(work.portfolio.book.equity(), 2),
        "backup": str(backup),
    }


def _verify_report(root: Path, ticker: str, session: str, receipt: dict, report: str) -> None:
    """Require exactly the model report bytes and hash, no path substitution."""
    from tradingagents.paper.conditional import evidence_hash

    expected = f"reports/{session}/{ticker}/source.json"
    saved = receipt.get("source_file")
    if not isinstance(saved, str) or saved.replace("\\", "/") != expected:
        raise ValueError("AI receipt does not bind to this exact ticker and session")
    pure = PurePosixPath(expected)
    source = root.joinpath(*pure.parts)
    if (
        source.is_symlink()
        or not source.is_file()
        or not source.resolve().is_relative_to(root.resolve())
        or source.read_bytes() != report.encode("utf-8")
        or receipt.get("source_sha256") != evidence_hash(report)
    ):
        raise ValueError("Model report missing or hash mismatched; no conditional order")


def _research_one(
    ledger: Path,
    session: str,
    names: list[str],
    source: dict,
    *,
    max_companies: int = 1,
    graph_factory=None,
    research_fn=None,
    completion_clock=None,
    config=None,
) -> dict:
    from tradingagents.paper.autopilot.daily_research import research_session

    return research_session(
        ledger,
        session,
        names,
        source,
        max_companies=max_companies,
        graph_factory=graph_factory,
        research_fn=research_fn,
        completion_clock=completion_clock,
        config=config,
    )


def run_once(
    ledger: Path,
    observer: Path,
    session: str,
    tickers: list[str],
    now: datetime,
    *,
    ai_budget: int = 0,
    write_state=None,
    graph_factory=None,
    research_fn=None,
    completion_clock=None,
    config=None,
) -> dict:
    """One current-session paper operation; NEVER auto-enables or creates books."""
    from tradingagents.paper.autopilot.daily_support import ledger_lock as exclusive

    if type(ai_budget) is not int or not 0 <= ai_budget <= 3:
        raise ValueError("Daily company AI budget must be 0-3")
    if completion_clock is None and research_fn is not None:

        def completion_clock():
            return now  # injected fake model uses injected test clock

    with exclusive(ledger):
        from .daily_transaction import recover_pending

        recover_pending(ledger)
        ready = inspect(ledger, observer, session, tickers, now)
        names, source, _ = _snapshot_sources(observer, session, tickers, now)
        status = (
            _commit_mark(ledger, observer, session, names, now, ready, write_state=write_state)
            if ready["new_session_to_mark"]
            else {"mark": "ALREADY_MARKED", "session": session}
        )
        if ai_budget:
            status.update(
                _research_one(
                    ledger,
                    session,
                    names,
                    source,
                    graph_factory=graph_factory,
                    research_fn=research_fn,
                    max_companies=ai_budget,
                    completion_clock=completion_clock,
                    config=config,
                )
            )
        else:
            status["research"] = "NOT_REQUESTED"
            status["model_attempts"] = 0
        status.update(
            {"schema": SCHEMA, "paper_only": True, "broker_connected": False, "session": session}
        )
        return status


def main(argv: list[str] | None = None) -> int:
    from tradingagents.paper.autopilot.observe import DEFAULT_OBSERVATION_HOME
    from tradingagents.paper.autopilot.state import STATE

    parser = argparse.ArgumentParser(
        description="Forward observer-backed PAPER simulator, NO BROKER"
    )
    parser.add_argument("--ledger", type=Path, default=STATE)
    parser.add_argument("--observer", type=Path, default=DEFAULT_OBSERVATION_HOME)
    parser.add_argument("--session", required=True)
    parser.add_argument("--tickers", nargs="+", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--run-once", action="store_true")
    parser.add_argument("--ai-budget", type=int, choices=(0, 1, 2, 3), default=0)
    args = parser.parse_args(argv)
    try:
        now = datetime.now(UTC)
        if args.check:
            if args.ai_budget:
                raise ValueError("Read-only preflight cannot request a paid model")
            result = inspect(args.ledger, args.observer, args.session, args.tickers, now)
        else:
            result = run_once(
                args.ledger,
                args.observer,
                args.session,
                args.tickers,
                now,
                ai_budget=args.ai_budget,
            )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except (
        ValueError,
        RuntimeError,
        OSError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        OverflowError,
    ) as exc:
        print("PAPER FORWARD BLOCKED: " + type(exc).__name__ + ": " + str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

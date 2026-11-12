"""Koral Equities: narrowly scoped Observer -> existing V3.1 PAPER baseline.

Requires same-day immutable source evidence, a flat DISABLED paper account,
explicit operator confirmation and complete backups. It cannot enable the
paper engine, issue intents, run models, or execute real/paper orders.

The first forward mark is a transparent prospective benchmark baseline. It
DOES NOT backfill missing historical prices or invent previous trades/P&L.

No action is permitted on a portfolio with an existing marked session,
open position, active/queued intent, AI decision or pending order. This is a
ONE-TIME bootstrap, not the subsequent autonomous trading scheduler.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SCHEMA = "koral-observer-paper-bootstrap-v1"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def timestamp(raw: str) -> datetime:
    if not isinstance(raw, str):
        raise ValueError("Missing original UTC observation timestamp")
    moment = datetime.fromisoformat(raw)
    if moment.tzinfo is None or moment.utcoffset() != timedelta(0):
        raise ValueError("Observation timestamp must be explicitly UTC")
    return moment


def same_ny_session(session: str, now: datetime) -> None:
    from .daily_support import session_windows

    try:
        day = date.fromisoformat(session)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid trading-session date") from exc
    if day.isoformat() != session or day.weekday() >= 5:
        raise ValueError("Not a canonical NY market weekday")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Timezone-aware real UTC clock required")
    ny = now.astimezone(NY)
    if ny.date() != day or ny.time() < session_windows(session)["close"][0]:
        raise ValueError("Not a SAME-SESSION completed NY market observation")


def calendar_bytes(
    observer: Path, session: str, phase: str, names: list[str], now: datetime
) -> bytes:
    """Validate the exact immutable NY cutoff and individual source timestamps."""
    if phase not in ("preopen", "preclose"):
        raise ValueError("Unsupported calendar phase")
    path = observer / "calendar" / session / (phase + ".json")
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing or unsafe " + phase + " observation")
    raw = path.read_bytes()
    if len(raw) > 1_000_000:
        raise ValueError("Oversized observation")
    entry = json.loads(raw)
    if (
        not isinstance(entry, dict)
        or entry.get("session") != session
        or entry.get("phase") != phase
        or entry.get("source_policy") != "TWO_SECONDARY_FEEDS_MUST_AGREE_OR_BLOCK"
        or not isinstance(entry.get("earnings"), dict)
        or set(entry["earnings"]) != set(names)
    ):
        raise ValueError("Calendar session, watchlist or provenance mismatch")
    end = timestamp(entry.get("observed_utc"))
    local = end.astimezone(NY)
    from .daily_support import session_windows

    lower, upper = session_windows(session)[phase]
    if (
        end > now.astimezone(UTC)
        or local.date().isoformat() != session
        or not lower <= local.time() <= upper
    ):
        raise ValueError("Calendar observation outside its original NY window")
    expected = {
        "asof_date",
        "next_earnings_date",
        "last_earnings_date",
        "source",
        "verified",
        "observed_at_utc",
    }
    for ticker, item in entry["earnings"].items():
        if not isinstance(item, dict) or set(item) != expected:
            raise ValueError("Missing or ambiguous issuer calendar fields: " + ticker)
        stamp = timestamp(item["observed_at_utc"])
        row_local = stamp.astimezone(NY)
        if (
            stamp > end
            or item["asof_date"] != session
            or row_local.date().isoformat() != session
            or not lower <= row_local.time() <= upper
            or type(item["verified"]) is not bool
        ):
            raise ValueError("Earnings source outside original NY cutoff or invalid as-of data")
        if item["verified"] is True and item["source"] != "YAHOO+NASDAQ_CONSENSUS_SECONDARY":
            raise ValueError("Unrecognised verified issuer source")
    return raw


def inspect_sources(observer: Path, session: str, names: list[str], now: datetime):
    """Re-use the existing strict source reader, never custom price rules."""
    from tradingagents.paper.autopilot import live_dry_run, observe

    same_ny_session(session, now)
    home = observe.observation_home(observer)
    source = live_dry_run.read_observation(home, session, names, now.astimezone(UTC))
    before = calendar_bytes(home, session, "preopen", names, now)
    after = calendar_bytes(home, session, "preclose", names, now)
    market = home / "market" / (session + ".json")
    if market.is_symlink() or sha(market.read_bytes()) != source["market_sha256"]:
        raise ValueError("Observed market bytes changed after validation")
    if source["calendar_sha256"] != sha(after):
        raise ValueError("After-close source changed after validation")
    return source, {
        "preopen.json": before,
        "preclose.json": after,
        "market.json": source["market_source_bytes"],
    }


def ensure_flat_disabled(state, session: str) -> None:
    book = state.portfolio.book
    if state.auto_paper_enabled or state.paused or book.halted:
        raise ValueError("Paper account must be disabled, unpaused and not risk-halted")
    if (
        book.positions
        or book.orders
        or state.portfolio.intents
        or state.ai_receipts
        or book.last_session is not None
        or book.equity_history
        or book.completed
    ):
        raise ValueError(
            "Existing account is not an untouched flat baseline; manual reconciliation required"
        )
    if (
        book.cash_gbp != 10000.0
        or book.equity() != 10000.0
        or state.portfolio.book.started > session
    ):
        raise ValueError("Baseline cash, equity or original start date invalid")


def _write_once(path: Path, data: bytes) -> bool:
    """Exclusive immutable publish; retry with identical evidence is harmless."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_symlink() or path.read_bytes() != data:
            raise ValueError("Existing paper archive is different: " + str(path))
        return False
    fd, tmp = tempfile.mkstemp(prefix=".source-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError as exc:
            raise ValueError("Concurrent paper evidence publication") from exc
    finally:
        Path(tmp).unlink(missing_ok=True)
    return True


def _atomic_restore(path: Path, data: bytes) -> None:
    fd, temp = tempfile.mkstemp(prefix=".restore-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def preview(ledger: Path, observer: Path, session: str, names: list[str], now: datetime) -> dict:
    from tradingagents.paper.autopilot.state import normalize_tickers, read

    # Match the proven Observer capacity, not the old eight-stock pilot
    # cardinality. Do not add unverified names merely to hit a target count.
    if normalize_tickers(names, cap=8) != names or not 4 <= len(names) <= 8:
        raise ValueError("Bootstrap requires 4-8 exact predeclared Observer stocks")
    state = read(ledger)
    ensure_flat_disabled(state, session)
    source, blobs = inspect_sources(observer, session, names, now)
    return {
        "status": "FORWARD_PAPER_BOOTSTRAP_ELIGIBLE_NO_ORDERS",
        "paper_only": True,
        "session": session,
        "roster_before": list(state.watchlist),
        "roster_after": names,
        "market_sha256": source["market_sha256"],
        "calendar_sha256": source["calendar_sha256"],
        "preopen_sha256": sha(blobs["preopen.json"]),
        "eligible_ai_count": sum(
            bool(x["eligible_for_research"]) for x in source["tickers"].values()
        ),
        "preopen_verified_count": sum(
            x["verified"] is True for x in json.loads(blobs["preopen.json"])["earnings"].values()
        ),
        "missing_history_note": "Original 22 Oct account was never marked; no historical P&L is invented",
        "paper_orders": 0,
        "models_called": 0,
        "broker_connected": False,
    }


def bootstrap(ledger: Path, observer: Path, session: str, names: list[str], now: datetime) -> dict:
    """One-time forward mark; never enables paper autonomy or places orders."""
    from tradingagents.paper.autopilot import runner
    from tradingagents.paper.autopilot.state import atomic_write, read
    from tradingagents.paper.cli import exclusive
    from tradingagents.paper.conditional import EarningsSnapshot

    with exclusive(ledger):
        old_bytes = ledger.read_bytes()
        state = read(ledger)
        ensure_flat_disabled(state, session)
        info = preview(ledger, observer, session, names, now)
        source, blobs = inspect_sources(observer, session, names, now)
        # A second validation is intentional only at this commit boundary:
        # it protects against an asynchronous source change during the write.
        if (
            info["market_sha256"] != source["market_sha256"]
            or info["calendar_sha256"] != source["calendar_sha256"]
            or info["preopen_sha256"] != sha(blobs["preopen.json"])
        ):
            raise ValueError("Source evidence changed during bootstrap")
        work = deepcopy(state)
        if work.watchlist != names:
            work.event(
                "OBSERVER_WATCHLIST_ADOPTED",
                session=session,
                previous=work.watchlist,
                current=names,
                forward_only=True,
            )
            work.watchlist = list(names)
        # Both calendars must have been honestly observed at the actual cutoff.
        # Verified=False remains an ineligible ticker, never a guessed approval.
        calendars = {}
        for phase in ("preopen", "preclose"):
            raw = json.loads(blobs[phase + ".json"])
            calendars[phase] = {
                ticker: EarningsSnapshot(**raw["earnings"][ticker]) for ticker in names
            }
        bars = source["bars"]
        fx_open, fx_close = source["fx_gbp_per_usd"]
        work.portfolio = work.portfolio.advance(
            session,
            {n: bars[n] for n in names},
            fx_open,
            fx_close,
            bars["SPY"].close,
            calendars["preopen"],
            calendars["preclose"],
        )
        if (
            work.portfolio.book.orders
            or work.portfolio.book.positions
            or work.portfolio.intents
            or work.portfolio.book.equity() != 10000.0
        ):
            raise ValueError("Baseline unexpectedly created simulated exposure or P&L")
        root = ledger.parent
        # Unique per-session evidence; never mutate or overwrite prior captures.
        copies = {root / "observer_sources" / session / name: data for name, data in blobs.items()}
        for phase in ("preopen", "preclose"):
            copies[root / "calendar" / session / (phase + ".json")] = blobs[phase + ".json"]
        archive_target = root / "market_evidence" / (session + ".json")
        if archive_target.exists():
            raise ValueError("Existing unlinked paper market evidence requires reconciliation")
        created = []
        backup_root = root / "_bridge_backups" / (session + "_" + sha(old_bytes)[:12])
        backup_path = backup_root / "portfolio_before.json"
        if backup_path.exists():
            if backup_path.read_bytes() != old_bytes:
                raise ValueError("Existing baseline backup does not match original account")
        else:
            if _write_once(backup_path, old_bytes):
                created.append(backup_path)
        committed = False
        try:
            for dest, content in copies.items():
                if _write_once(dest, content):
                    created.append(dest)
            archive_digest = runner.archive_market_evidence(
                ledger,
                session=session,
                now=now,
                bars={n: bars[n] for n in names},
                fx_open=fx_open,
                fx_close=fx_close,
                spy_close=bars["SPY"].close,
            )
            created.append(archive_target)
            for phase in ("preopen", "preclose"):
                key = phase + ".json"
                work.event(
                    "EARNINGS_SNAPSHOT_CAPTURED",
                    session=session,
                    phase=phase,
                    sha256=sha(blobs[key]),
                    imported_from_observer=True,
                    original_observed_utc=json.loads(blobs[key])["observed_utc"],
                )
            work.event(
                "OBSERVER_SOURCE_BOUND",
                date=session,
                origin="IMMUTABLE_OBSERVER_SOURCE_NOT_EXCHANGE_CERTIFIED",
                market_sha256=sha(blobs["market.json"]),
                preopen_sha256=sha(blobs["preopen.json"]),
                preclose_sha256=sha(blobs["preclose.json"]),
                paper_market_sha256=archive_digest,
                source_roster=names,
                simulated_orders=0,
            )
            work.event(
                "FORWARD_BASELINE_ONLY",
                date=session,
                original_start=state.portfolio.book.started,
                notice="No backfilled trades, prices, or P&L from missing sessions",
            )
            work.event(
                "SESSION_MARKED",
                date=session,
                market_input_sha256=archive_digest,
                backfill=False,
                equity_gbp=10000.0,
                new_order_count=0,
                note="First actual same-session paper-only benchmark observation",
            )
            if ledger.read_bytes() != old_bytes:
                raise ValueError("Account changed concurrently before baseline write")
            try:
                atomic_write(ledger, work)
                reread = read(ledger)
                if (
                    reread.auto_paper_enabled
                    or reread.portfolio.book.orders
                    or reread.portfolio.book.positions
                    or reread.portfolio.book.last_session != session
                    or reread.portfolio.book.equity() != 10000.0
                ):
                    raise ValueError("Post-commit account verification failed")
                committed = True
            except BaseException:
                # Even a failed atomic writer may have replaced the ledger
                # before reporting an error. Reinstate exact old bytes.
                if not ledger.exists() or ledger.read_bytes() != old_bytes:
                    _atomic_restore(ledger, old_bytes)
                raise
        finally:
            if not committed:
                for item in reversed(created):
                    # Do not delete the durable backup. Evidence created by an
                    # aborted bootstrap is unreferenced and can be revalidated.
                    if item != backup_path:
                        item.unlink(missing_ok=True)
        return {
            **info,
            "status": "FORWARD_PAPER_BASELINE_BOUND",
            "ledger_sha256": sha(ledger.read_bytes()),
            "backup": str(backup_path),
            "auto_paper_enabled": False,
        }


def main(argv: list[str] | None = None) -> int:
    from tradingagents.paper.autopilot.observe import DEFAULT_OBSERVATION_HOME
    from tradingagents.paper.autopilot.state import STATE, normalize_tickers

    p = argparse.ArgumentParser(
        description="One-time forward source-backed PAPER baseline; no orders"
    )
    p.add_argument("--ledger", type=Path, default=STATE)
    p.add_argument("--observer", type=Path, default=DEFAULT_OBSERVATION_HOME)
    p.add_argument("--session", required=True)
    p.add_argument("--tickers", nargs="+", required=True)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply-disabled-baseline", action="store_true")
    args = p.parse_args(argv)
    try:
        symbols = normalize_tickers(args.tickers, cap=8)
        now = datetime.now(UTC)
        outcome = (
            preview(args.ledger, args.observer, args.session, symbols, now)
            if args.check
            else bootstrap(args.ledger, args.observer, args.session, symbols, now)
        )
        print(json.dumps(outcome, indent=2, sort_keys=True))
        return 0
    except (OSError, TypeError, ValueError, RuntimeError, KeyError, OverflowError) as exc:
        print("PAPER BRIDGE BLOCKED: " + type(exc).__name__ + ": " + str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

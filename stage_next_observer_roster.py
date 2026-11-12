"""One safe forward-session Observer roster from an already saved Swing scan.

Writes ONLY a new observation roster. Does not inspect or alter paper accounts,
market/earnings captures, existing scheduler configuration, model settings or
broker connections. Explicit --apply required. If preview quality or source
integrity fails, NO roster is created. No historical approval/backdating.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SCHEMA = "koral-predeclared-research-roster-v1"
QUEUE_SCHEMA = "koral-swing-research-queue-v1"
DEFAULT_ROOT = Path.home() / ".tradingagents" / "swing_research"
DEFAULT_OBSERVER = Path.home() / ".tradingagents" / "observations" / "koral_v3"
SCRIPT = "KE_Observer_Scheduler_2026-10-26.py"
TICKER = re.compile(r"[A-Z][A-Z0-9.-]{0,11}\Z")


def _dt(value: str) -> datetime:
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid source UTC timestamp") from exc
    if moment.tzinfo is None or moment.utcoffset() != timedelta(0):
        raise ValueError("Source timestamp must be explicit UTC")
    return moment.astimezone(UTC)


def _date(value: str) -> date:
    try:
        d = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid canonical session") from exc
    if d.isoformat() != value or d.weekday() >= 5:
        raise ValueError("Noncanonical or weekend session")
    return d


def _next_weekday(day: date) -> date:
    following = day + timedelta(days=1)
    while following.weekday() >= 5:
        following += timedelta(days=1)
    return following


def _load_scheduler(project: Path):
    filename = project / SCRIPT
    if filename.is_symlink() or not filename.is_file():
        raise ValueError("Verified roster-aware Scheduler script is missing")
    spec = importlib.util.spec_from_file_location("ke_roster_scheduler_checked", filename)
    if spec is None or spec.loader is None:
        raise ValueError("Scheduler module is not loadable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "watchlist_for_session", None)):
        raise ValueError("Installed Observer has no future-session roster support")
    return module


def inspect(
    *, project: Path, root: Path, observer: Path, source_day: str, target_day: str, now: datetime
) -> tuple[dict, bytes]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Real UTC clock required")
    utc_now = now.astimezone(UTC)
    source, target = _date(source_day), _date(target_day)
    if _next_weekday(source) != target:
        raise ValueError("Roster must be for the immediate NEXT weekday session")
    if not 1 <= (target - source).days <= 4:
        raise ValueError("Source queue too old")
    cutoff = datetime.combine(target, time(8, 45), tzinfo=NY).astimezone(UTC)
    if utc_now >= cutoff:
        raise ValueError("Approval is past the next session's preopen cutoff")
    folder = (root / "runs" / source_day).resolve()
    queue = folder / "queue.json"
    if queue.is_symlink() or not queue.is_file():
        raise ValueError("Immutable source queue missing or unsafe")
    source_bytes = queue.read_bytes()
    if len(source_bytes) > 2_000_000:
        raise ValueError("Oversized scanner queue")
    record = json.loads(source_bytes)
    if (
        not isinstance(record, dict)
        or record.get("schema") != QUEUE_SCHEMA
        or record.get("status") != "RESEARCH_QUEUE_ONLY_NO_TRADE_AUTHORITY"
        or record.get("session") != source_day
        or record.get("model_calls") != 0
        or record.get("paper_orders") != 0
        or record.get("portfolio_reads") != 0
        or record.get("portfolio_writes") != 0
        or record.get("broker_connected") is not False
    ):
        raise ValueError("Source is not a brokerless, no-AI Swing Alpha queue")
    generated = _dt(record.get("generated_utc"))
    started = _dt(record.get("scan_started_utc"))
    if (
        generated > utc_now
        or started > generated
        or started.astimezone(NY).date() != source
        or started.astimezone(NY).time() < time(17, 40)
        or generated.astimezone(NY).date() != source
    ):
        raise ValueError("Queue was not genuinely generated after the source NY close")
    if (
        record.get("earliest_next_weekday") != target_day
        or record.get("earnings_preview_policy")
        != "TWO_SECONDARY_CONSENSUS_PREVIEW_NOT_NEXT_SESSION_AUTHORITY"
    ):
        raise ValueError("Next-session earnings screening was not performed")
    names = record.get("next_session_research_watchlist")
    rows = record.get("candidates")
    # The fixed eight-stock rule was an arbitrary pilot ceiling, not a
    # risk-management floor. Keep at least four names (broader than the old
    # three-stock pilot) and never fill vacant places with unverified stocks.
    if (
        not isinstance(names, list)
        or not 4 <= len(names) <= 8
        or any(type(t) is not str or not TICKER.fullmatch(t) for t in names)
        or len(set(names)) != len(names)
        or not isinstance(rows, list)
        or len(rows) != len(names)
        or record.get("candidate_count") != len(names)
    ):
        raise ValueError("Require 4-8 unique earnings-previewed candidates, never padded")
    if [r.get("ticker") for r in rows if isinstance(r, dict)] != names:
        raise ValueError("Roster order does not match source candidate records")
    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("asof") != source_day
            or row.get("earnings_verification") != "NOT_CAPTURED_FOR_NEXT_SESSION"
            or row.get("execution_authority") != "NONE_RESEARCH_ONLY"
        ):
            raise ValueError("Candidate is not evidence-limited research")
        preview = row.get("earnings_preview")
        if (
            not isinstance(preview, dict)
            or preview.get("source") != "YAHOO+NASDAQ_CONSENSUS_SECONDARY_PREVIEW_ONLY"
            or preview.get("asof") != source_day
            or preview.get("earliest_next_weekday") != target_day
            or preview.get("next_weekday_is_not_holiday_certified") is not True
        ):
            raise ValueError("Unverified next-session earnings preview")
        last, nxt = (
            _date(preview.get("last_earnings_date")),
            _date(preview.get("next_earnings_date")),
        )
        if last > source or (target - last).days <= 2 or (nxt - target).days <= 7:
            raise ValueError("Earnings blackout or stale preview")
        observed = _dt(preview.get("observed_at_utc"))
        if observed > generated or observed < started - timedelta(minutes=2):
            raise ValueError("Earnings preview timestamp inconsistent with scan")
    destination = root / "rosters" / (target_day + ".json")
    if destination.exists() or destination.is_symlink():
        raise ValueError("Target roster already exists; never overwrite a declared session")
    for path in (
        observer / "market" / (target_day + ".json"),
        observer / "calendar" / target_day / "preopen.json",
        observer / "calendar" / target_day / "preclose.json",
    ):
        if path.exists() or path.is_symlink():
            raise ValueError("Target day was already observed; cannot reassign tickers")
    scheduler = _load_scheduler(project)
    if tuple(getattr(scheduler, "WATCHLIST", ())) != ("COST", "JPM", "WMT"):
        raise ValueError("Installed scheduler is not the expected safe observer version")
    summary = {
        "schema": SCHEMA,
        "session": target_day,
        "source_session": source_day,
        "queue_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "approved_utc": utc_now.isoformat(),
        "watchlist": names,
        "mode": "OBSERVE_ONLY",
        "trading_enabled": False,
        "approved_by_operator": True,
        "notice": "Bounded research-only roster; no account, model or broker permissions",
    }
    return summary, source_bytes


def stage(
    *, project: Path, root: Path, observer: Path, source_day: str, target_day: str, now: datetime
) -> dict:
    record, _ = inspect(
        project=project,
        root=root,
        observer=observer,
        source_day=source_day,
        target_day=target_day,
        now=now,
    )
    path = root / "rosters" / (target_day + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    fd, temp = tempfile.mkstemp(prefix=".ke-roster-", suffix=".tmp", dir=path.parent)
    published = False
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.link(temp, path)  # exclusive, same volume; no overwrite
        published = True
    finally:
        Path(temp).unlink(missing_ok=True)
    try:
        scheduler = _load_scheduler(project)
        if scheduler.watchlist_for_session(
            target_day, rosters=root / "rosters", queues=root / "runs"
        ) != tuple(record["watchlist"]):
            raise ValueError("Installed scheduler did not independently accept the staged roster")
    except BaseException:
        if published and path.is_file() and path.read_bytes() == data:
            path.unlink()
        raise
    return {
        "status": "NEXT_SESSION_BOUNDED_ROSTER_STAGED_OBSERVE_ONLY",
        "session": target_day,
        "source_session": source_day,
        "watchlist": record["watchlist"],
        "count": len(record["watchlist"]),
        "queue_sha256": record["queue_sha256"],
        "paper_reads": 0,
        "paper_writes": 0,
        "paper_orders": 0,
        "broker_connected": False,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Stage a source-backed next-session 4-8-stock Observer roster"
    )
    p.add_argument("--source-session", required=True)
    p.add_argument("--session", required=True)
    p.add_argument("--project", type=Path, default=Path(__file__).resolve().parent)
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--observer", type=Path, default=DEFAULT_OBSERVER)
    x = p.add_mutually_exclusive_group(required=True)
    x.add_argument("--check", action="store_true")
    x.add_argument("--apply", action="store_true")
    args = p.parse_args(argv)
    try:
        if args.check:
            record, _ = inspect(
                project=args.project,
                root=args.root,
                observer=args.observer,
                source_day=args.source_session,
                target_day=args.session,
                now=datetime.now(UTC),
            )
            result = {
                "status": "NEXT_SESSION_ROSTER_PREFLIGHT_PASS",
                **record,
                "note": "No files changed; --apply required to stage observation roster",
            }
        else:
            result = stage(
                project=args.project,
                root=args.root,
                observer=args.observer,
                source_day=args.source_session,
                target_day=args.session,
                now=datetime.now(UTC),
            )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except (ValueError, KeyError, RuntimeError, OSError, TypeError, json.JSONDecodeError) as exc:
        print("ROSTER_STAGE_BLOCKED: " + type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
